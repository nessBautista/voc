#!/usr/bin/env python3
"""Serve two web revisions in an isolated Docker instance; preserve installed binaries."""

import argparse
import hashlib
import json
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / "data/desktop-web-reload"
CONTAINER = "voc-desktop-web-reload"
LABEL = "dev.voc.acceptance.web-reload"
URL = "http://127.0.0.1:8093"
EXECUTABLES = ("Contents/MacOS/VOCDataState", "Contents/Helpers/VOCDesktopBridge")


def execute(*arguments):
    return subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
        cwd=REPO,
    ).stdout.strip()


def binary_hashes(app):
    return {
        name: hashlib.sha256((app / name).read_bytes()).hexdigest()
        for name in EXECUTABLES
    }


def assert_same_binaries(config):
    if binary_hashes(Path(config["installed_app"])) != config["executable_sha256"]:
        raise ValueError(
            "Installed binaries changed; this run cannot prove web-only updating."
        )


def render_revision(original, revision):
    if revision not in ("A", "B") or "<body>" not in original:
        raise ValueError("Expected revision A/B and a compiled HTML body.")
    marker = f'<aside role="status" style="padding:16px;background:#fff1bb;color:#171717;font:18px system-ui">Prueba web · versión {revision} · mismo cliente Mac</aside>'
    return original.replace("<body>", "<body>" + marker, 1)


def container_present():
    return bool(execute("docker", "ps", "-aq", "--filter", f"name=^/{CONTAINER}$"))


def assert_owned(config):
    if (
        execute(
            "docker",
            "inspect",
            "--format",
            f'{{{{index .Config.Labels "{LABEL}"}}}}',
            CONTAINER,
        )
        != config["run_id"]
    ):
        raise ValueError(
            "Container is not owned by this acceptance run; nothing was stopped."
        )


def read_url(path):
    with urllib.request.urlopen(URL + path, timeout=3) as response:
        return response.read()


def wait_ready():
    for _ in range(30):
        try:
            return json.loads(read_url("/health/ready"))
        except (OSError, ValueError):
            time.sleep(1)
    raise RuntimeError(
        "Acceptance server did not become ready; inspect its container logs."
    )


def check(config):
    assert_owned(config)
    assert_same_binaries(config)
    ready = json.loads(read_url("/health/ready"))
    if ready["publication_id"] != config["publication_id"]:
        raise ValueError("Unexpected publication in acceptance server.")
    html = read_url("/")
    marker = f"Prueba web · versión {config['revision']} · mismo cliente Mac".encode()
    if marker not in html:
        raise ValueError("The server is not serving the expected web revision.")
    if (
        execute("docker", "inspect", "--format", "{{.Image}}", CONTAINER)
        != config["image"]
    ):
        raise ValueError("Acceptance container image changed.")
    evidence = {
        "url": URL,
        "revision": config["revision"],
        "publication_id": ready["publication_id"],
        "web_sha256": hashlib.sha256(html).hexdigest(),
        "native_binaries_unchanged": True,
        "executable_sha256": config["executable_sha256"],
        "visual_confirmation": "Confirm the banner in the installed Mac application.",
    }
    (Path(config["directory"]) / f"evidence-{config['revision']}.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    print(json.dumps(evidence, indent=2))


def start(app):
    if container_present():
        raise ValueError(
            "Acceptance container already exists. Use check or stop first."
        )
    if ROOT.is_symlink():
        raise ValueError("Acceptance directory must not be a symlink.")
    hashes = binary_hashes(app)
    identity = execute(
        "docker",
        "compose",
        "--project-directory",
        str(REPO),
        "-p",
        "voc-state-app",
        "-f",
        "docker/app/compose.yaml",
        "ps",
        "-q",
        "app",
    )
    if not identity or "\n" in identity:
        raise ValueError("Start the primary app with just app-up first.")
    image = execute("docker", "inspect", "--format", "{{.Image}}", identity)
    run_id = str(uuid.uuid4())
    directory = ROOT / run_id
    directory.mkdir(parents=True)
    state = directory / "state"
    state.mkdir(mode=0o777)
    state.chmod(0o777)  # The app image runs as uid 10001, not the Mac account.
    (state / "active.json").write_bytes(
        (REPO / "data/voc-app/state/active.json").read_bytes()
    )
    web = directory / "web"
    execute("docker", "cp", f"{identity}:/app/web", str(web))
    original = (web / "index.html").read_text()
    (directory / "original.html").write_text(original)
    (web / "index.html").write_text(render_revision(original, "A"))
    config = {
        "run_id": run_id,
        "directory": str(directory),
        "image": image,
        "installed_app": str(app),
        "executable_sha256": hashes,
        "revision": "A",
    }
    # Save ownership before Docker starts so an interrupted setup can still be stopped.
    (ROOT / "current.json").write_text(json.dumps(config, indent=2) + "\n")
    execute(
        "docker",
        "run",
        "--detach",
        "--init",
        "--name",
        CONTAINER,
        "--label",
        f"{LABEL}={run_id}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--publish",
        "127.0.0.1:8093:8000",
        "--mount",
        f"type=bind,source={REPO / 'data/voc-app/catalog'},target=/app/catalog,readonly",
        "--mount",
        f"type=bind,source={state},target=/app/state",
        "--mount",
        f"type=bind,source={web},target=/app/web,readonly",
        "--env",
        "VOC_PUBLICATIONS_DIR=/app/catalog",
        "--env",
        "VOC_APP_STATE_DIR=/app/state",
        "--env",
        "VOC_WEB_DIR=/app/web",
        "--env",
        "VOC_PLANNER_MODE=fixture",
        "--env",
        "VOC_LLM_API_KEY=",
        "--env",
        "VOC_MCP_ENABLED=false",
        "--env",
        "VOC_MCP_TOKEN=",
        image,
    )
    config["publication_id"] = wait_ready()["publication_id"]
    (ROOT / "current.json").write_text(json.dumps(config, indent=2) + "\n")
    check(config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "update", "check", "stop"))
    parser.add_argument(
        "--app", type=Path, default=Path("/Applications/VOCDataState.app")
    )
    args = parser.parse_args()
    if args.action == "start":
        start(args.app.expanduser().resolve())
        return
    config = json.loads((ROOT / "current.json").read_text())
    if args.action == "stop":
        if container_present():
            assert_owned(config)
            execute("docker", "stop", CONTAINER)
            execute("docker", "rm", CONTAINER)
        print(
            "Acceptance container stopped; evidence preserved. Primary app unchanged."
        )
        return
    if args.action == "update":
        assert_owned(config)
        assert_same_binaries(config)
        directory = Path(config["directory"])
        target = directory / "web/index.html"
        temporary = target.with_suffix(".next.html")
        temporary.write_text(
            render_revision((directory / "original.html").read_text(), "B")
        )
        temporary.replace(target)
        config["revision"] = "B"
        (ROOT / "current.json").write_text(json.dumps(config, indent=2) + "\n")
    check(config)


if __name__ == "__main__":
    main()
