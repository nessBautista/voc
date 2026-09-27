"""Exercise host launchers with synthetic secrets and fake Docker/1Password."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def host(tmp_path):
    project = tmp_path / "fresh checkout"
    project.mkdir()
    shutil.copytree(ROOT / "scripts", project / "scripts")
    shutil.copy(ROOT / "Justfile", project / "Justfile")
    config = project / "config"
    config.mkdir()
    credentials = config / "aws.local.env"
    # Treat values as data, never shell code. This would write a marker if sourced.
    credentials.write_text("AWS_SECRET_ACCESS_KEY='$(touch UNSAFE)'\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['LAUNCH_RECORD']).write_text(json.dumps({\n"
        " 'args': sys.argv[1:],\n"
        " 'env': {k: v for k, v in os.environ.items() if k.startswith(('AWS_', 'VOC_', 'COMPOSE_', 'JUPYTER_'))}\n"
        "}))\n"
    )
    docker.chmod(0o755)
    op = binaries / "op"
    op.write_text(
        "#!/bin/sh\n"
        'touch "$OP_CALLED"\n'
        'while [ "$1" != -- ]; do shift; done\nshift\n'
        "export AWS_ACCESS_KEY_ID=synthetic-key-id\n"
        "export AWS_SECRET_ACCESS_KEY=synthetic-secret\n"
        "export AWS_REGION=us-east-2\n"
        'exec "$@"\n'
    )
    op.chmod(0o755)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AWS_", "VOC_", "COMPOSE_", "JUPYTER_", "JUST_"))
    }
    env.update(
        PATH=str(binaries) + os.pathsep + os.environ["PATH"],
        LAUNCH_RECORD=str(tmp_path / "launch.json"),
        OP_CALLED=str(tmp_path / "op-called"),
    )
    return project, env


def launch(host, *, mode=None, command=None, extra=None):
    project, env = host
    env = env | (extra or {})
    if mode is not None:
        env["VOC_AWS_AUTH_MODE"] = mode
    result = subprocess.run(
        command
        or ["sh", "scripts/start-aws.sh", "docker", "compose", "-f", "compose.yaml"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    record = Path(env["LAUNCH_RECORD"])
    return result, json.loads(record.read_text()) if record.exists() else None


def test_file_mode_never_calls_op_or_sources_secrets_and_clears_stale_identity(host):
    result, record = launch(
        host,
        mode="env-file",
        extra={
            "AWS_ACCESS_KEY_ID": "old-terminal-id",
            "AWS_SECRET_ACCESS_KEY": "old-terminal-secret",
            "AWS_SESSION_TOKEN": "old-session",
            "VOC_MEMBER_ID": "old-member",
            "VOC_ARTIFACT_DESTINATION": "local",
            "VOC_DATASET_SOURCE": "local",
            "COMPOSE_PROJECT_NAME": "voc-onboarding",
            "JUPYTER_TOKEN": "synthetic-login",
        },
    )
    assert result.returncode == 0, result.stderr
    assert record["args"] == [
        "compose",
        "-f",
        "compose.yaml",
        "--env-file",
        "config/aws.local.env",
        "-f",
        "compose.aws-credentials.yaml",
        "up",
        "--build",
        "-d",
        "--wait",
    ]
    assert "AWS_ACCESS_KEY_ID" not in record["env"]
    assert "AWS_SECRET_ACCESS_KEY" not in record["env"]
    assert "AWS_SESSION_TOKEN" not in record["env"]
    assert "VOC_MEMBER_ID" not in record["env"]
    assert "VOC_ARTIFACT_DESTINATION" not in record["env"]
    assert record["env"]["COMPOSE_PROJECT_NAME"] == "voc-onboarding"
    assert record["env"]["JUPYTER_TOKEN"] == "synthetic-login"
    assert not Path(host[1]["OP_CALLED"]).exists()
    assert not (host[0] / "UNSAFE").exists()
    assert "old-terminal-secret" not in result.stdout + result.stderr


def test_file_mode_preserves_path_with_spaces(host):
    source = host[0] / "config/aws.local.env"
    target = host[0] / "config/other credentials.local.env"
    source.rename(target)
    result, record = launch(
        host, mode="env-file", extra={"VOC_AWS_ENV_FILE": str(target)}
    )
    assert result.returncode == 0
    assert record["args"][record["args"].index("--env-file") + 1] == str(target)


@pytest.mark.parametrize("mode", ["env-file", "invalid"])
def test_missing_file_or_unknown_mode_stops_before_docker(host, mode):
    (host[0] / "config/aws.local.env").unlink()
    result, record = launch(host, mode=mode)
    assert result.returncode != 0
    assert record is None
    assert not Path(host[1]["OP_CALLED"]).exists()


def test_existing_onepassword_mode_still_injects_validated_environment(host):
    result, record = launch(host)
    assert result.returncode == 0, result.stderr
    assert Path(host[1]["OP_CALLED"]).exists()
    assert record["env"]["AWS_SECRET_ACCESS_KEY"] == "synthetic-secret"
    assert "synthetic-secret" not in str(record["args"]) + result.stdout + result.stderr
    assert "--env-file" not in record["args"]


@pytest.mark.parametrize("secret", ["", "op://unresolved/secret"])
def test_op_validator_rejects_missing_or_unresolved_secret(host, secret):
    result, record = launch(
        host,
        command=["sh", "scripts/up-aws.sh", "docker", "compose"],
        extra={
            "AWS_ACCESS_KEY_ID": "synthetic-id",
            "AWS_SECRET_ACCESS_KEY": secret,
            "AWS_REGION": "us-east-2",
        },
    )
    assert result.returncode != 0
    assert record is None
    assert "op://unresolved/secret" not in result.stdout + result.stderr


@pytest.mark.skipif(
    not shutil.which("just"), reason="Host just executable is not installed"
)
@pytest.mark.parametrize("seed", ["", "/host/seed directory"])
@pytest.mark.parametrize("storage", ["bind", "volume"])
def test_just_up_loads_dotenv_and_only_mounts_seed_for_publishers(host, seed, storage):
    (host[0] / ".env").write_text(
        "VOC_AWS_AUTH_MODE=env-file\nCOMPOSE_PROJECT_NAME=voc-onboarding\n"
        "JUPYTER_TOKEN=synthetic-login\n"
        f"VOC_SEED_DIR='{seed}'\nVOC_STORAGE_MODE={storage}\n"
    )
    result, record = launch(host, command=["just", "up"])
    assert result.returncode == 0, result.stderr
    assert ("compose.seed.yaml" in record["args"]) == bool(seed)
    assert ("compose.bind.yaml" in record["args"]) == (storage == "bind")
    assert record["env"]["COMPOSE_PROJECT_NAME"] == "voc-onboarding"
    assert record["env"]["JUPYTER_TOKEN"] == "synthetic-login"
    assert not Path(host[1]["OP_CALLED"]).exists()


@pytest.mark.skipif(
    not shutil.which("just"), reason="Host just executable is not installed"
)
@pytest.mark.parametrize("recipe", ["down", "shell", "status", "up-local"])
def test_management_and_local_start_need_no_credentials(host, recipe):
    (host[0] / "config/aws.local.env").unlink()
    (host[0] / ".env").write_text(
        "VOC_AWS_AUTH_MODE=env-file\nJUPYTER_TOKEN=synthetic-login\n"
    )
    result, record = launch(host, command=["just", recipe])
    assert result.returncode == 0, result.stderr
    assert "compose.aws-credentials.yaml" not in record["args"]
    assert not Path(host[1]["OP_CALLED"]).exists()
    if recipe == "up-local":
        assert record["env"]["VOC_ARTIFACT_DESTINATION"] == "local"
        assert record["env"]["VOC_DATASET_SOURCE"] == "local"
