"""Mac-side acceptance commands; use only Python stdlib and the existing Docker image."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / "data/app-cycle"


def execute(arguments, *, env=None, capture=False, source=None):
    """Pass arguments directly; do not interpolate credentials into a shell command."""
    return subprocess.run(
        arguments,
        cwd=REPO,
        env=env,
        input=source,
        text=True,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        timeout=180,
    ).stdout


def compose(file, project=None):
    arguments = ["docker", "compose", "--project-directory", str(REPO)]
    if project:
        arguments += ["-p", project]
    return arguments + ["-f", file]


def container_id(arguments):
    result = execute(arguments + ["ps", "-q", "app"], capture=True).strip()
    if not result or "\n" in result:
        raise RuntimeError("Start the primary application with just app-up first")
    return result


def inspect(identity, template):
    return execute(
        ["docker", "inspect", "--format", template, identity], capture=True
    ).strip()


def primary_digest():
    return hashlib.sha256(
        (REPO / "data/voc-app/state/active.json").read_bytes()
    ).hexdigest()


def operator(action):
    execute(
        compose("docker/operator/compose.yaml")
        + [
            "exec",
            "-T",
            "workspace",
            "python",
            "tests/acceptance/data_state_cycle/cycle_publications.py",
            action,
        ]
    )


def prepare_runtime(image, before):
    """Finish an interrupted setup only after the Operator validates its A/B files."""
    if ROOT.is_symlink():
        raise ValueError("Acceptance directory cannot be a symlink")
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / "state/cycle.json").is_file():
        operator("verify")
    else:
        operator("prepare")
    private = ROOT / "private.env"
    if not private.exists():
        descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as target:
            target.write("VOC_MCP_TOKEN=" + secrets.token_urlsafe(48) + "\n")
    (ROOT / "runner.json").write_text(
        json.dumps({"image": image, "primary_selection_sha256": before}, indent=2)
    )


def main(action):
    config_file = ROOT / "runner.json"
    if action == "up" and not config_file.exists():
        # Resolve a running app's immutable image ID before creating acceptance state.
        identity = container_id(compose("docker/app/compose.yaml", "voc-state-app"))
        image = inspect(identity, "{{.Image}}")
        before = primary_digest()
        prepare_runtime(image, before)
    config = json.loads(config_file.read_text())
    # The dedicated compose file never loads the root .env or the production secrets.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VOC_", "COMPOSE_", "AWS_", "OPENROUTER_"))
    }
    env.update(
        VOC_CYCLE_IMAGE=config["image"],
        VOC_CYCLE_PRIVATE_FILE=str(ROOT / "private.env"),
    )
    command = compose(
        "tests/acceptance/data_state_cycle/compose.yaml", "voc-state-cycle"
    ) + [
        "--env-file",
        "/dev/null",
    ]
    if action == "stop":
        execute(command + ["down"], env=env)
        return
    if action in ("up", "recreate"):
        options = ["--force-recreate"] if action == "recreate" else []
        execute(
            command + ["up", "-d", "--no-build", "--pull", "never", "--wait", *options],
            env=env,
        )
    elif action == "restart":
        execute(command + ["restart", "app"], env=env)
        execute(
            command + ["up", "-d", "--no-build", "--pull", "never", "--wait"], env=env
        )
    elif action in ("a", "b", "real"):
        operator(action)
    elif action == "independence":
        running = execute(
            compose("docker/operator/compose.yaml")
            + ["ps", "--status", "running", "-q", "workspace"],
            capture=True,
        ).strip()
        if running:
            raise RuntimeError(
                "Stop only the idle Operator workspace before this check"
            )
    elif action != "check":
        raise ValueError("Unknown acceptance action")
    # Read only selected Docker fields: never dump the container environment.
    identity = execute(command + ["ps", "-q", "app"], env=env, capture=True).strip()
    if not identity or inspect(identity, "{{.Image}}") != config["image"]:
        raise RuntimeError("Acceptance image changed")
    if (
        inspect(
            identity,
            '{{range .Mounts}}{{if eq .Destination "/app/catalog"}}{{.RW}}{{end}}{{end}}',
        )
        != "false"
    ):
        raise RuntimeError("Acceptance catalog must be mounted read-only")
    if inspect(identity, "{{.HostConfig.ReadonlyRootfs}}") != "true":
        raise RuntimeError("Acceptance root filesystem must be read-only")
    if primary_digest() != config["primary_selection_sha256"]:
        raise RuntimeError("Primary selection changed; review before continuing")
    if action == "up":
        print("Acceptance ready: http://127.0.0.1:8092 · fixture planner")
        next_action = "check" if (ROOT / "state/cycle-evidence.json").exists() else "a"
        print(f"Next: just app-cycle {next_action}")
        return
    probe_action = "check" if action in ("restart", "recreate") else action
    source = (REPO / "tests/acceptance/data_state_cycle/cycle_probe.py").read_text()
    execute(
        command + ["exec", "-T", "app", "python", "-", probe_action],
        env=env,
        source=source,
    )
    print("Same application image; primary selection unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "up",
            "a",
            "b",
            "check",
            "restart",
            "recreate",
            "independence",
            "real",
            "stop",
        ],
    )
    arguments = parser.parse_args()
    try:
        main(arguments.action)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from error
