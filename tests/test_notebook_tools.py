"""Offline checks for first-run settings and notebook creation/browser commands."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import nbformat
import pytest

from src.mlops.notebooks import create_notebook, editor_url

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "configure_voc", ROOT / "scripts/configure.py"
)
configure_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure_module)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "notebooks/templates").mkdir(parents=True)
    for name in (
        ".env.example",
        "config/aws.local.env.example",
        "notebooks/templates/shared-dataset.py",
    ):
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def test_setup_generates_private_settings_and_preserves_them_on_rerun(project, capsys):
    created = configure_module.configure(project, "Alice Example")
    assert all(created.values())
    env = (project / ".env").read_text()
    token = next(
        line.split("=", 1)[1]
        for line in env.splitlines()
        if line.startswith("JUPYTER_TOKEN=")
    )
    assert len(token) == 48
    credentials = project / "config/aws.local.env"
    assert "VOC_MEMBER_ID='alice-example'" in credentials.read_text()
    credentials.write_text("AWS_SECRET_ACCESS_KEY='synthetic-existing-secret'\n")
    assert not any(configure_module.configure(project, "Another User").values())
    assert (project / ".env").read_text() == env
    assert "synthetic-existing-secret" in credentials.read_text()
    output = capsys.readouterr().out
    assert token not in output and "synthetic-existing-secret" not in output
    assert (project / ".env").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("kind,suffix", [("marimo", ".py"), ("jupyter", ".ipynb")])
def test_nested_notebook_creation_and_repeated_open_preserve_work(
    project, kind, suffix
):
    root = project / "notebooks"
    relative, created = create_notebook(root, "alice/my-notebook", kind)
    assert created and relative == "alice/my-notebook" + suffix
    content = (root / relative).read_text()
    if kind == "jupyter":
        notebook = nbformat.reads(content, as_version=4)
        nbformat.validate(notebook)
        assert all(not cell.get("outputs") for cell in notebook.cells)
    else:
        compile(content, relative, "exec")
    (root / relative).write_text(content + "\n")
    assert create_notebook(root, "alice/my-notebook" + suffix, kind) == (
        relative,
        False,
    )
    assert (root / relative).read_text() == content + "\n"


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/tmp/escape",
        "alice/../../escape",
        "alice\\escape",
        "alice//test",
        "CON/test",
        "a/test.ipynb",
        "$(touch PWNED)",
    ],
)
def test_invalid_marimo_names_are_rejected_without_writes(project, name):
    with pytest.raises(ValueError):
        create_notebook(project / "notebooks", name, "marimo")
    assert not (project / "PWNED").exists()


def test_symlink_escape_is_rejected(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (project / "notebooks/link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        create_notebook(project / "notebooks", "link/escape", "marimo")
    assert not list(outside.iterdir())


@pytest.mark.parametrize(
    "kind,port,field", [("marimo", 2720, "access_token"), ("jupyter", 8889, "token")]
)
def test_urls_use_host_ports_nested_names_and_encoded_tokens(kind, port, field):
    relative = "alice/my-notebook.py" if kind == "marimo" else "alice/my-notebook.ipynb"
    parsed = urlparse(editor_url(kind, port, relative, "synthetic&token=+"))
    assert parsed.port == port
    query = parse_qs(parsed.query)
    assert query[field] == ["synthetic&token=+"]
    if kind == "marimo":
        assert query["file"] == [relative]
    else:
        assert parsed.path == "/lab/tree/" + relative


@pytest.mark.skipif(
    not shutil.which("just"), reason="Requires the host just executable"
)
@pytest.mark.parametrize(
    "recipe,suffix", [("new-marimo", ".py"), ("new-notebook", ".ipynb")]
)
def test_just_creates_files_without_echoing_browser_token(project, recipe, suffix):
    shutil.copy(ROOT / "Justfile", project / "Justfile")
    shutil.copytree(ROOT / "scripts", project / "scripts")
    (project / ".env").write_text(
        "JUPYTER_TOKEN=synthetic-browser-secret\nVOC_MARIMO_PORT=2720\nVOC_JUPYTER_PORT=8889\n"
    )
    binaries = project / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from src.mlops import notebooks\n"
        f"notebooks.NOTEBOOKS=Path({str(project / 'notebooks')!r})\n"
        "notebooks.main(sys.argv[sys.argv.index('src.mlops.notebooks')+1:])\n"
    )
    docker.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("VOC_", "JUPYTER_", "JUST_"))
    }
    env.update(PATH=str(binaries) + os.pathsep + env["PATH"], VOC_NO_BROWSER="1")
    result = subprocess.run(
        ["just", recipe, "alice/new-one"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (project / ("notebooks/alice/new-one" + suffix)).exists()
    assert "synthetic-browser-secret" not in result.stdout + result.stderr
    assert "127.0.0.1:" in result.stdout
    if recipe == "new-marimo":
        assert "file=alice%2Fnew-one.py" in result.stdout
    malicious = subprocess.run(
        ["just", recipe, "$(touch PWNED)"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert malicious.returncode != 0
    assert not (project / "PWNED").exists()


def test_macos_setup_flow_with_fake_host_tools_preserves_credentials(project):
    shutil.copytree(ROOT / "scripts", project / "scripts")
    binaries = project / "fake-host"
    binaries.mkdir()
    for name, body in {
        "uname": "echo Darwin",
        "brew": "echo Unexpected host installation >&2; exit 99",
        "just": "exit 0",
        "open": '[ "$1" = -e ] || exit 99',
    }.items():
        path = binaries / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    # Existing Docker-compatible runtime works without Docker.app or a package manager.
    docker = binaries / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys\n"
        "if sys.argv[1] == 'info':\n"
        "    print('linux')\n"
        "elif sys.argv[1] == 'run':\n"
        "    args=sys.argv[sys.argv.index('python')+1:]\n"
        "    raise SystemExit(subprocess.call([sys.executable, *args]))\n"
    )
    docker.chmod(0o755)
    env = os.environ | {"PATH": str(binaries) + os.pathsep + os.environ["PATH"]}
    for _ in range(2):
        result = subprocess.run(
            ["bash", "scripts/setup.sh"],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "Setup complete" in result.stdout
        path = project / "config/aws.local.env"
        if _ == 0:
            path.write_text("AWS_SECRET_ACCESS_KEY='synthetic-installed-secret'\n")
        else:
            assert "synthetic-installed-secret" in path.read_text()
            assert "synthetic-installed-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["missing-just", "stopped-runtime", "missing-compose", "windows-containers"])
def test_macos_setup_requires_prerequisites_without_installing_or_writing(project, failure):
    shutil.copytree(ROOT / "scripts", project / "scripts")
    binaries = project / "prerequisites"
    binaries.mkdir()
    # Isolate PATH so a real host executable cannot satisfy a missing prerequisite.
    (binaries / "dirname").symlink_to(shutil.which("dirname"))
    bodies = {
        "uname": "echo Darwin",
        "git": "exit 0",
        "brew": "touch SHOULD_NOT_INSTALL; exit 99",
        "docker": "exit 99",
    }
    if failure != "missing-just":
        bodies["just"] = "exit 0"
    if failure == "stopped-runtime":
        bodies["docker"] = "exit 1"
    elif failure == "missing-compose":
        bodies["docker"] = '[ "$1" = info ]'
    elif failure == "windows-containers":
        bodies["docker"] = 'if [ "$1" = info ]; then echo windows; fi'
    for name, body in bodies.items():
        path = binaries / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    result = subprocess.run(
        [shutil.which("bash"), "scripts/setup.sh"], cwd=project,
        env=os.environ | {"PATH": str(binaries)}, capture_output=True, text=True,
    )
    assert result.returncode != 0
    expected = {
        "missing-just": "Missing prerequisite: just",
        "stopped-runtime": "Container runtime is not ready",
        "missing-compose": "Docker Compose v2 is required",
        "windows-containers": "Linux-container runtime",
    }
    assert expected[failure] in result.stderr
    assert not (project / ".env").exists()
    assert not (project / "config/aws.local.env").exists()
    assert not (project / "SHOULD_NOT_INSTALL").exists()
