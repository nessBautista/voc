"""Behavioral coverage of the secret-free host configuration bridge."""
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/workspace.py"))
load_configuration = MODULE["load_configuration"]
ConfigurationError = MODULE["ConfigurationError"]


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/workspace.toml").write_text("auth_mode = 'env-file'\n")
    (tmp_path / "config/storage.toml").write_text("region = 'eu-west-1'\nbucket = 'private-bucket'\n")
    (tmp_path / ".env").write_text("JUPYTER_TOKEN=private-token\n")
    (tmp_path / "config/aws.local.env").write_text("AWS_ACCESS_KEY_ID=private-key\nAWS_SECRET_ACCESS_KEY='private-secret'\n")
    return tmp_path


def test_defaults_emit_only_nonsecret_records(root):
    records = load_configuration(root, credentials=True)
    assert not (MODULE["SECRET_KEYS"] | {"JUPYTER_TOKEN"}) & records.keys()
    assert "private" not in str(records)
    assert records["VOC_WORKSPACE_BUILD_TARGET"] == "workspace"


def test_local_overrides_ports_region_and_spaced_path(root):
    (root / "config/workspace.local.toml").write_text("jupyter_port = 9991\ncredential_file = 'config/my keys.env'\n")
    (root / "config/storage.local.toml").write_text("region = 'us-east-2'\n")
    (root / "config/my keys.env").write_text("AWS_SESSION_TOKEN=literal\n")
    records = load_configuration(root, credentials=True)
    assert records["VOC_JUPYTER_PORT"] == "9991"
    assert records["VOC_WORKSPACE_CREDENTIAL_FILE"] == "config/my keys.env"
    assert records["AWS_DEFAULT_REGION"] == "us-east-2"


@pytest.mark.parametrize("editor,target", [("none", "workspace"), ("nvim", "nvim")])
def test_editor_selection(root, editor, target):
    (root / "config/workspace.toml").write_text(f"editor = '{editor}'\n")
    assert load_configuration(root)["VOC_WORKSPACE_BUILD_TARGET"] == target


@pytest.mark.parametrize("base,local,target", [
    ("none", "nvim", "nvim"),
    ("nvim", "none", "workspace"),
])
def test_local_editor_overrides_tracked_selection(root, base, local, target):
    (root / "config/workspace.toml").write_text(f"editor = '{base}'\n")
    (root / "config/workspace.local.toml").write_text(f"editor = '{local}'\n")
    assert load_configuration(root)["VOC_WORKSPACE_BUILD_TARGET"] == target


@pytest.mark.parametrize("value", ["'vim'", "'NVIM'", "''", "true", "1", "[]", "{}"])
def test_editor_requires_supported_string(root, value):
    (root / "config/workspace.local.toml").write_text(f"editor = {value}\n")
    with pytest.raises(ConfigurationError, match="editor must be none or nvim"):
        load_configuration(root)


@pytest.mark.parametrize("setting", [
    "unknown = 'ignored'", "dashboard_port = 8250", "auth_mode = 'automatic'",
    "jupyter_port = 0", "jupyter_port = 65536", "jupyter_port = true",
    "jupyter_port = '8891'", "jupyter_port = 2722",
    "credential_file = '../outside.env'", "credential_file = '/tmp/outside.env'",
    "credential_file = 'data/keys.env'", 'credential_file = "config/line\\nbreak.env"',
])
def test_invalid_workspace_settings_fail(root, setting):
    (root / "config/workspace.local.toml").write_text(setting + "\n")
    with pytest.raises(ConfigurationError):
        load_configuration(root)


def test_symlink_escape_rejected_even_without_credentials(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("external") / "keys.env"
    outside.write_text("AWS_ACCESS_KEY_ID=not-disclosed\n")
    (root / "config/aws.local.env").unlink()
    (root / "config/aws.local.env").symlink_to(outside)
    with pytest.raises(ConfigurationError, match="symlinks"):
        load_configuration(root)


def test_management_does_not_read_credentials(root):
    (root / "config/aws.local.env").unlink()
    (root / "config/workspace.local.toml").write_text("auth_mode = '1password'\n")
    assert load_configuration(root)["VOC_WORKSPACE_AUTH_MODE"] == "1password"
    with pytest.raises(ConfigurationError, match="missing"):
        load_configuration(root, credentials=True)


def test_op_references_require_selected_mode(root):
    (root / "config/aws.local.env").write_text("AWS_SECRET_ACCESS_KEY=op://vault/item/password\n")
    with pytest.raises(ConfigurationError, match="1password"):
        load_configuration(root, credentials=True)
    (root / "config/workspace.local.toml").write_text("auth_mode = '1password'\n")
    assert load_configuration(root, credentials=True)["VOC_WORKSPACE_AUTH_MODE"] == "1password"


@pytest.mark.parametrize("entry", ["AWS_BUCKET=undisclosed", "VOC_DASHBOARD_PORT=undisclosed", "export JUPYTER_TOKEN=undisclosed", "$(touch undisclosed)"])
def test_stale_or_executable_root_env_fails_without_values(root, entry):
    (root / ".env").write_text("JUPYTER_TOKEN=token\n" + entry + "\n")
    with pytest.raises(ConfigurationError, match="rerun setup/configure") as caught:
        load_configuration(root)
    assert "undisclosed" not in str(caught.value)


@pytest.mark.parametrize("entry", ["AWS_PROFILE=undisclosed", "AWS_ACCESS_KEY_ID='unterminated", "AWS_ACCESS_KEY_ID=first\nAWS_ACCESS_KEY_ID=second", "AWS_ACCESS_KEY_ID=${INHERITED_SECRET}"])
def test_unsupported_secret_entries_fail(root, entry):
    (root / "config/aws.local.env").write_text(entry + "\n")
    with pytest.raises(ConfigurationError):
        load_configuration(root, credentials=True)


def test_dollar_literals_are_data(root):
    (root / "config/aws.local.env").write_text("AWS_SECRET_ACCESS_KEY='$(not-a-command)'\n")
    assert load_configuration(root, credentials=True)["VOC_WORKSPACE_AUTH_MODE"] == "env-file"


def test_environment_does_not_override_workspace_selection(root, monkeypatch):
    monkeypatch.setenv("VOC_JUPYTER_PORT", "1234")
    monkeypatch.setenv("AWS_REGION", "wrong-region")
    monkeypatch.setenv("VOC_STORAGE_CONFIG", "/outside.toml")
    monkeypatch.setenv("VOC_WORKSPACE_BUILD_TARGET", "nvim")
    records = load_configuration(root)
    assert records["VOC_JUPYTER_PORT"] == "8891"
    assert records["AWS_REGION"] == "eu-west-1"
    assert records["VOC_WORKSPACE_BUILD_TARGET"] == "workspace"


def test_invalid_toml_does_not_disclose_contents(root):
    (root / "config/workspace.local.toml").write_text("undisclosed malformed input")
    with pytest.raises(ConfigurationError) as caught:
        load_configuration(root)
    assert "undisclosed" not in str(caught.value)


@pytest.mark.parametrize("region", ['"us-east-1\\ninjected=value"', "23", "[]"])
def test_invalid_region_rejected(root, region):
    (root / "config/storage.local.toml").write_text(f"region = {region}\n")
    with pytest.raises(ConfigurationError, match="region"):
        load_configuration(root)

def test_credential_suffix_protects_build_context(root):
    (root / "config/workspace.local.toml").write_text("credential_file = 'config/credentials.txt'\n")
    with pytest.raises(ConfigurationError, match="relocate"):
        load_configuration(root)


def test_credential_symlink_target_requires_excluded_suffix(root):
    target = root / "config/credentials.txt"
    target.write_text("AWS_ACCESS_KEY_ID=undisclosed\n")
    (root / "config/aws.local.env").unlink()
    (root / "config/aws.local.env").symlink_to(target)
    with pytest.raises(ConfigurationError, match="symlink targets"):
        load_configuration(root)


@pytest.mark.parametrize("command", ["up", "build", "down", "ps", "logs", "shell"])
@pytest.mark.parametrize("editor,inherited,target", [
    ("none", "nvim", "workspace"),
    ("nvim", "workspace", "nvim"),
])
def test_launcher_exports_validated_target_to_compose(root, monkeypatch, command, editor, inherited, target):
    repo = Path(__file__).resolve().parents[2]
    scripts = root / "scripts"
    scripts.mkdir()
    for name in ("workspace.py", "workspace.sh"):
        shutil.copyfile(repo / "scripts" / name, scripts / name)
    (root / "config/workspace.local.toml").write_text(f"editor = '{editor}'\n")
    binaries = root / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, runpy, sys\n"
        "if sys.argv[1] == 'run':\n"
        "    assert 'VOC_WORKSPACE_BUILD_TARGET' not in os.environ\n"
        f"    helper = {str(scripts / 'workspace.py')!r}\n"
        f"    sys.argv = [helper, '--root', {str(root)!r}] + "
        "(['--credentials'] if '--credentials' in sys.argv else [])\n"
        "    runpy.run_path(helper, run_name='__main__')\n"
        "elif sys.argv[1] == 'compose':\n"
        "    print(json.dumps({'target': os.environ.get('VOC_WORKSPACE_BUILD_TARGET'), "
        "'args': sys.argv[2:]}))\n"
        "else:\n"
        "    raise SystemExit('Unexpected Docker invocation')\n"
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("VOC_WORKSPACE_BUILD_TARGET", inherited)
    monkeypatch.delenv("MSYSTEM", raising=False)
    result = subprocess.run(
        ["sh", str(scripts / "workspace.sh"), command],
        capture_output=True, text=True, check=True,
    )
    consumer = json.loads(result.stdout)
    assert consumer["target"] == target
    args = consumer["args"]
    assert args[args.index("-p") + 1] == "voc-main"
    assert args[args.index("-f") + 1] == str(root / "compose.yaml")
    expected = ["exec", "workspace", "bash"] if command == "shell" else [command]
    assert args[args.index("-p") + 2:] == expected
