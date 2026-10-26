"""Exercise private setup and fail-closed migration using synthetic files only."""

import importlib.util
import os
import shutil
import stat
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("configuration_setup", REPO / "scripts/configure.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "checkout"
    (root / "config").mkdir(parents=True)
    for name in (".env.example", "config/aws.local.env.example", "config/workspace.toml", "config/storage.toml"):
        shutil.copyfile(REPO / name, root / name)
    return root


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def toml(root, name):
    return tomllib.loads((root / "config" / name).read_text())


def test_fresh_private_setup_and_rerun_preserve_user_choices(project, capsys):
    result = setup.configure(project, "Alice Example!")
    assert all(result.values())
    env = (project / ".env").read_bytes()
    token = env.split(b"JUPYTER_TOKEN=", 1)[1].strip()
    assert token and b"VOC_" not in env
    assert toml(project, "storage.local.toml") == {"member_id": "alice-example"}
    assert toml(project, "workspace.local.toml") == {}
    assert toml(project, "workspace.toml")["editor"] == "none"
    for name in result:
        assert stat.S_IMODE((project / name).stat().st_mode) == 0o600
    credential = project / "config/aws.local.env"
    credential.write_bytes(b"AWS_SECRET_ACCESS_KEY='synthetic-private-value'\r\n")
    before = snapshot(project)
    assert not any(setup.configure(project, "personal").values())
    assert snapshot(project) == before
    assert "synthetic-private-value" not in capsys.readouterr().out
    assert not (project / "data").exists()


def test_migration_preserves_secrets_and_captures_all_active_settings(project, capsys):
    credentials = b"# untouched\r\nAWS_SECRET_ACCESS_KEY='synthetic-private-value'\r\n"
    (project / "config/team keys.local.env").write_bytes(credentials)
    secret_lines = b"# notebook secret\r\nJUPYTER_TOKEN='synthetic-token-$literal'\r\n"
    original = secret_lines + (
        b"COMPOSE_PROJECT_NAME=voc-main\r\n"
        b"AWS_BUCKET='team-fixture-bucket'\r\nAWS_REGION=us-east-2\r\n"
        b"VOC_MEMBER_ID=existing-member\r\nVOC_DATASET_SOURCE=s3\r\n"
        b"VOC_ARTIFACT_DESTINATION=s3\r\nVOC_EMBEDDINGS_PREFIX=fixture/embeddings\r\n"
        b"VOC_AWS_AUTH_MODE='1password'\r\n"
        b"VOC_AWS_ENV_FILE='config/team keys.local.env' # literal path\r\n"
        b"VOC_ZENML_PORT=18240\r\nVOC_MLFLOW_PORT=15003\r\n"
        b"VOC_JUPYTER_PORT=18891\r\nVOC_MARIMO_PORT=12722\r\n"
        b"VOC_DASHBOARD_PORT=18052\r\n"
    )
    (project / ".env").write_bytes(original)
    (project / "data").mkdir()
    (project / "data/sentinel").write_bytes(b"protected runtime data")
    setup.configure(project, "personal")
    assert (project / ".env").read_bytes() == secret_lines
    assert (project / ".env.before-toml").read_bytes() == original
    assert stat.S_IMODE((project / ".env.before-toml").stat().st_mode) == 0o600
    assert (project / "config/team keys.local.env").read_bytes() == credentials
    assert not (project / "config/aws.local.env").exists()
    assert toml(project, "workspace.local.toml") == {
        "auth_mode": "1password", "credential_file": "config/team keys.local.env",
        "zenml_port": 18240, "mlflow_port": 15003, "jupyter_port": 18891, "marimo_port": 12722,
    }
    assert toml(project, "storage.local.toml") == {
        "bucket": "team-fixture-bucket", "region": "us-east-2", "member_id": "existing-member",
        "dataset_source": "s3", "artifact_destination": "s3", "embeddings_prefix": "fixture/embeddings",
    }
    before = snapshot(project)
    setup.configure(project, "personal")
    assert snapshot(project) == before
    output = capsys.readouterr().out
    assert "Retired VOC_DASHBOARD_PORT" in output
    assert all(value not in output for value in ("synthetic-private-value", "synthetic-token", "18052", "team-fixture-bucket"))
    assert (project / "data/sentinel").read_bytes() == b"protected runtime data"
    assert list((project / "data").iterdir()) == [project / "data/sentinel"]


@pytest.mark.parametrize("assignment", [
    "UNRECOGNIZED=private-fixture", "AWS_REGION=${REGION}", "AWS_REGION=$(touch sentinel)",
    "AWS_REGION=`touch sentinel`", "COMPOSE_PROJECT_NAME=another-project",
    "VOC_JUPYTER_PORT=not-a-port", "VOC_JUPYTER_PORT=0", "VOC_ZENML_PORT=5003",
    "VOC_AWS_ENV_FILE=/tmp/credentials.env", "VOC_AWS_ENV_FILE=../credentials.env",
    "VOC_AWS_ENV_FILE=data/credentials.env", "VOC_AWS_AUTH_MODE=unsupported",
    'AWS_REGION="$(touch sentinel)"', "AWS_REGION='${REGION}'",
    "VOC_AWS_ENV_FILE=config/credentials.txt",
    "VOC_AWS_ENV_FILE=C:/private/credentials.env",
])
def test_invalid_migration_refuses_before_any_writes(project, assignment, capsys):
    (project / ".env").write_text("JUPYTER_TOKEN='private-fixture'\n" + assignment + "\n")
    before = snapshot(project)
    with pytest.raises(ValueError) as error:
        setup.configure(project, "alice")
    assert snapshot(project) == before
    assert "private-fixture" not in str(error.value) + capsys.readouterr().out
    assert not (project / "sentinel").exists()


def test_conflicting_local_values_refuse_all_writes(project):
    (project / ".env").write_text("JUPYTER_TOKEN=private-fixture\nAWS_REGION=us-east-2\nVOC_JUPYTER_PORT=18891\n")
    (project / "config/workspace.local.toml").write_text("jupyter_port = 18892\n")
    before = snapshot(project)
    with pytest.raises(ValueError, match="Conflicting jupyter_port"):
        setup.configure(project, "alice")
    assert snapshot(project) == before


def test_matching_partial_migration_is_restartable(project, monkeypatch):
    original = b"JUPYTER_TOKEN=private-fixture\nAWS_REGION=us-east-2\nVOC_MEMBER_ID=alice\n"
    (project / ".env").write_bytes(original)
    publish = setup.publish

    def interrupted(path, content, previous):
        if path.name == ".env":
            raise OSError("synthetic interrupted final publish")
        publish(path, content, previous)

    monkeypatch.setattr(setup, "publish", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        setup.configure(project, "different")
    assert (project / ".env").read_bytes() == original
    assert toml(project, "storage.local.toml")["member_id"] == "alice"
    monkeypatch.setattr(setup, "publish", publish)
    setup.configure(project, "personal")
    assert (project / ".env").read_bytes() == b"JUPYTER_TOKEN=private-fixture\n"
    assert (project / ".env.before-toml").read_bytes() == original
    assert toml(project, "storage.local.toml")["member_id"] == "alice"


def test_existing_backup_never_overwritten(project):
    (project / ".env").write_text("AWS_REGION=us-east-2\nJUPYTER_TOKEN=private-fixture\n")
    (project / ".env.before-toml").write_text("previous private recovery file\n")
    before = snapshot(project)
    with pytest.raises(ValueError, match="before-toml"):
        setup.configure(project, "alice")
    assert snapshot(project) == before


@pytest.mark.skipif(os.name == "nt", reason="Unix symlink fixture")
@pytest.mark.parametrize("name", [".env", "config/workspace.local.toml", "config/storage.local.toml", "config/aws.local.env"])
def test_symlinks_refused_without_touching_targets(project, tmp_path, name):
    target = tmp_path / "outside"
    target.write_text("# private target\n")
    (project / name).symlink_to(target)
    before = snapshot(project)
    with pytest.raises(ValueError, match="symlink"):
        setup.configure(project, "alice")
    assert target.read_text() == "# private target\n"
    assert snapshot(project) == before


def test_existing_custom_selection_and_member_survive_setup(project):
    (project / "config/workspace.local.toml").write_text('credential_file = "config/selected.local.env"\n')
    (project / "config/storage.local.toml").write_text('member_id = "existing"\n')
    (project / "config/selected.local.env").write_text("AWS_ACCESS_KEY_ID='private-fixture'\n")
    setup.configure(project, "personal")
    assert not (project / "config/aws.local.env").exists()
    assert toml(project, "storage.local.toml")["member_id"] == "existing"
    assert (project / "config/selected.local.env").read_text() == "AWS_ACCESS_KEY_ID='private-fixture'\n"


@pytest.mark.parametrize("content", ['unknown = "value"\n', "auth_mode = 42\n", "invalid TOML ["])
def test_invalid_local_toml_refuses_before_private_files(project, content):
    (project / "config/workspace.local.toml").write_text(content)
    before = snapshot(project)
    with pytest.raises(ValueError):
        setup.configure(project, "alice")
    assert snapshot(project) == before


def test_invalid_credential_parent_refuses_before_other_writes(project):
    (project / "config/not-a-directory").write_text("keep me")
    (project / "config/workspace.local.toml").write_text(
        'credential_file = "config/not-a-directory/keys.local.env"\n'
    )
    before = snapshot(project)
    with pytest.raises(ValueError, match="directory"):
        setup.configure(project, "alice")
    assert snapshot(project) == before


@pytest.mark.parametrize("editor", ["none", "nvim"])
def test_setup_preserves_personal_editor_selection(project, editor):
    local = project / "config/workspace.local.toml"
    original = f'# Personal editor choice\neditor = "{editor}"\n'.encode()
    local.write_bytes(original)
    setup.configure(project, "alice")
    assert local.read_bytes() == original
    before = snapshot(project)
    assert not any(setup.configure(project, "alice").values())
    assert snapshot(project) == before


def test_setup_accepts_existing_configuration_without_editor(project):
    tracked = project / "config/workspace.toml"
    tracked.write_text("".join(
        line for line in tracked.read_text().splitlines(keepends=True)
        if not line.startswith("editor =")
    ))
    setup.configure(project, "alice")
    assert "editor" not in toml(project, "workspace.toml")
    assert toml(project, "workspace.local.toml") == {}


@pytest.mark.parametrize("value", ["'vim'", "'NVIM'", "''", "true", "1", "[]", "{}"])
def test_setup_rejects_invalid_editor_before_writes(project, value):
    (project / "config/workspace.local.toml").write_text(f"editor = {value}\n")
    before = snapshot(project)
    with pytest.raises(ValueError):
        setup.configure(project, "alice")
    assert snapshot(project) == before
