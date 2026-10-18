"""Run the acceptance commands against real files, sockets and restarted processes."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from runpy import run_path

import httpx
import pytest
import yaml
from conftest import REPO

helpers = run_path(
    str(REPO / "tests/acceptance/data_state_cycle/cycle_publications.py")
)


def test_cycle_isolated_compose_has_no_build_or_provider_credentials():
    config = yaml.safe_load(
        (REPO / "tests/acceptance/data_state_cycle/compose.yaml").read_text()
    )
    app = config["services"]["app"]
    assert config["name"] == "voc-state-cycle"
    assert "build" not in app and "depends_on" not in app
    assert app["environment"]["VOC_PLANNER_MODE"] == "fixture"
    assert app["environment"]["VOC_LLM_API_KEY"] == ""
    assert app["ports"] == ["127.0.0.1:8092:8000"]
    assert app["tmpfs"] == ["/tmp:rw,noexec,nosuid,size=64m"]
    assert app["read_only"] is True
    assert app["volumes"][0]["read_only"] is True
    assert {m["source"] for m in app["volumes"]} == {
        "./data/app-cycle/catalog",
        "./data/app-cycle/state",
    }


def test_existing_acceptance_directory_is_never_overwritten(tmp_path):
    marker = tmp_path / "keep"
    marker.write_text("existing user material")
    with pytest.raises(FileExistsError):
        helpers["prepare"](tmp_path)
    assert marker.read_text() == "existing user material"


def test_cli_http_mcp_cycle_survives_restart_and_restores_real_copy(tmp_path):
    root = tmp_path / "cycle"
    info = helpers["prepare"](root)
    # A separate catalog stands in for the primary; no activation may change it.
    from voc.publications.store import activate_bundle

    primary_catalog, primary_state = (
        tmp_path / "primary-catalog",
        tmp_path / "primary-state",
    )
    activate_bundle(root / "bundles/a", primary_catalog, primary_state)
    primary_pointer = (primary_state / "active.json").read_bytes()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("VOC_", "AWS_", "OPENROUTER_", "LANGSMITH_", "LANGCHAIN_")
        )
    }
    env.update(
        PATH=str(Path(sys.executable).parent) + os.pathsep + env["PATH"],
        VOC_PUBLICATIONS_DIR=str(root / "catalog"),
        VOC_APP_STATE_DIR=str(root / "state"),
        VOC_PLANNER_MODE="fixture",
        VOC_LLM_API_KEY="",
        VOC_INTERNAL_API_URL=origin,
        VOC_MCP_ENABLED="true",
        VOC_MCP_TOKEN="acceptance-test-only-not-a-real-secret-00001",
        VOC_MCP_ALLOWED_HOSTS=f"127.0.0.1:{port}",
        VOC_MCP_ALLOWED_ORIGINS=origin,
    )

    def start():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "voc_app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(150):
            try:
                if (
                    httpx.get(
                        origin + "/health/ready", trust_env=False, timeout=0.2
                    ).status_code
                    == 200
                ):
                    return process
            except httpx.HTTPError:
                pass
            if process.poll() is not None:
                raise AssertionError("Acceptance server exited")
            time.sleep(0.05)
        process.kill()
        process.wait()
        raise AssertionError("Acceptance server did not start")

    def probe(action):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO / "tests/acceptance/data_state_cycle/cycle_probe.py"),
                action,
            ],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        )
        return json.loads(result.stdout)

    process = start()
    try:
        first = probe("a")
        assert first["publication_id"] == info["publications"]["a"]["publication_id"]
        helpers["select"](root, "b")  # Also rejects corrupt and unsupported copies.
        second = probe("b")
        assert second["active"] == "b"
        assert first["conversations"]["a"] == second["conversations"]["a"]
        before = (root / "state/cycle-evidence.json").read_bytes()
        process.terminate()
        process.wait(timeout=10)
        process = start()
        probe("check")
        assert (root / "state/cycle-evidence.json").read_bytes() == before
        probe("independence")
        helpers["select"](root, "real", primary_catalog, primary_state)
        assert probe("real")["model_requests"] == 0
        assert (primary_state / "active.json").read_bytes() == primary_pointer
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("already_prepared", [False, True])
def test_host_setup_preserves_or_prepares_bundles(
    tmp_path, monkeypatch, already_prepared
):
    root = tmp_path / "prepared"
    if already_prepared:
        helpers["prepare"](root)
    driver = run_path(str(REPO / "tests/acceptance/data_state_cycle/cycle.py"))
    initialize = driver["prepare_runtime"]
    monkeypatch.setitem(initialize.__globals__, "ROOT", root)
    calls = []

    def operator(action):
        calls.append(action)
        if action == "prepare":
            helpers["prepare"](root)
        else:
            assert action == "verify"
            helpers["verify_prepared"](root)

    monkeypatch.setitem(initialize.__globals__, "operator", operator)
    initialize("sha256:test-image", "primary-digest")
    info = json.loads((root / "state/cycle.json").read_text())
    token_file = root / "private.env"
    original = token_file.read_bytes()
    assert token_file.stat().st_mode & 0o777 == 0o600
    initialize("sha256:test-image", "primary-digest")
    assert token_file.read_bytes() == original
    assert json.loads((root / "state/cycle.json").read_text()) == info
    assert calls == (
        ["verify", "verify"] if already_prepared else ["prepare", "verify"]
    )
    assert (
        json.loads((root / "runner.json").read_text())["image"] == "sha256:test-image"
    )


def test_prepared_state_with_changed_digest_cannot_resume(tmp_path):
    root = tmp_path / "changed"
    info = helpers["prepare"](root)
    package = root / "catalog" / info["publications"]["a"]["publication_id"]
    with (package / "publication.duckdb").open("ab") as target:
        target.write(b"changed")
    with pytest.raises((ValueError, RuntimeError)):
        helpers["verify_prepared"](root)
