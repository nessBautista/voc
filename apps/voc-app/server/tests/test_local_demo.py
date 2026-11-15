"""Team demo: atomic S3 install, offline startup, and isolated host launchers."""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from voc.publications.store import activate_bundle, current_publication

REPO = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "demo_data", REPO / "scripts/demo_data.py"
)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def selection_for(bundle):
    payload = (bundle / "manifest.json").read_bytes()
    return {
        "bucket": "synthetic-demo-bucket",
        "region": "us-east-2",
        "publication_id": json.loads(payload)["publication_id"],
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
    }


class FakeS3:
    def __init__(self, bundle, *, fail=False, corrupt=False, manifest=None):
        self.bundle, self.fail, self.corrupt = bundle, fail, corrupt
        self.manifest = manifest
        self.calls = []
        self.body = None

    def get_object(self, *, Bucket, Key):
        self.calls.append(("get", Bucket, Key))
        content = (
            self.manifest
            if self.manifest is not None
            else (self.bundle / "manifest.json").read_bytes()
        )
        self.raw_body = io.BytesIO(content)
        self.body = StreamingBody(self.raw_body, len(content))
        return {"Body": self.body}

    def download_file(self, bucket, key, filename, **kwargs):
        self.calls.append(("download", bucket, key))
        if self.fail:
            Path(filename).write_bytes(b"partial")
            raise ClientError({"Error": {"Code": "ExpiredToken"}}, "GetObject")
        if self.corrupt:
            Path(filename).write_bytes(b"corrupted database")
        else:
            shutil.copyfile(self.bundle / "publication.duckdb", filename)


def test_install_then_offline_replay(v2_bundle, tmp_path, monkeypatch):
    selection = selection_for(v2_bundle)
    client = FakeS3(v2_bundle)
    catalog, state = tmp_path / "catalog", tmp_path / "state"
    first = demo.sync(selection, catalog, state, client=client)
    prefix = f"publications/{selection['publication_id']}/"
    assert client.calls == [
        ("get", selection["bucket"], prefix + "manifest.json"),
        ("download", selection["bucket"], prefix + "publication.duckdb"),
    ]
    assert client.raw_body.closed
    monkeypatch.setattr(
        demo, "s3_client", lambda _: pytest.fail("Must not contact AWS")
    )
    assert demo.sync(selection, catalog, state) == first
    assert demo.verify(selection, catalog, state) == first
    assert sorted(p.name for p in catalog.iterdir() if p.is_dir()) == [
        selection["publication_id"]
    ]


@pytest.mark.parametrize("failure", ["manifest", "download", "database"])
def test_failed_update_preserves_previous_publication_and_conversations(
    failure, v2_bundle, bundles, tmp_path
):
    catalog, state = tmp_path / "catalog", tmp_path / "state"
    before = activate_bundle(bundles[0], catalog, state)
    (state / "conversation-sentinel").write_text("keep")
    client = FakeS3(
        v2_bundle,
        fail=failure == "download",
        corrupt=failure == "database",
        manifest=b"{}" if failure == "manifest" else None,
    )
    with pytest.raises((ValueError, ClientError)):
        demo.sync(selection_for(v2_bundle), catalog, state, client=client)
    assert current_publication(state) == before
    assert (state / "conversation-sentinel").read_text() == "keep"
    assert not (catalog / selection_for(v2_bundle)["publication_id"]).exists()
    if failure == "manifest":
        assert len(client.calls) == 1


def test_invalid_pin_rejected_before_credentials_or_io(v2_bundle, tmp_path):
    selection = selection_for(v2_bundle)
    path = tmp_path / "selection.json"
    for field, value in [
        ("region", "us-east-1"),
        ("publication_id", "../../other"),
        ("manifest_sha256", "bad"),
        ("bucket", "s3://wrong"),
    ]:
        path.write_text(json.dumps(selection | {field: value}))
        with pytest.raises(ValueError):
            demo.load_selection(path)


def test_sync_error_does_not_echo_credentials(v2_bundle, tmp_path, monkeypatch, capsys):
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection_for(v2_bundle)))

    def denied(*args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "secret-sentinel"}},
            "GetObject",
        )

    monkeypatch.setattr(demo, "sync", denied)
    assert demo.main(["sync", "--selection", str(path)]) == 1
    output = capsys.readouterr()
    assert "secret-sentinel" not in output.err
    assert "responsable" in output.err


def test_local_dashboard_chat_and_history_after_restart(
    v2_bundle, tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient
    from test_chat import ask
    from voc_app.main import create_app
    from voc_app.settings import Settings

    catalog, state = tmp_path / "catalog", tmp_path / "state"
    selected = demo.sync(
        selection_for(v2_bundle), catalog, state, client=FakeS3(v2_bundle)
    )
    monkeypatch.setattr(demo, "s3_client", lambda _: pytest.fail("No S3 at runtime"))
    settings = Settings(catalog, state)  # fixture planner: no paid model calls
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/publications/current").status_code == 200
        response = client.get(
            f"/api/v1/publications/{selected['publication_id']}/summary"
        )
        assert response.status_code == 200, response.text
        conversation = ask(
            client,
            {"request_id": str(uuid4()), "question": "¿Cuál es la queja principal?"},
        )
        assert conversation["turns"][-1]["status"] == "completed"
        identity = conversation["conversation_id"]
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/v1/conversations/{identity}").json() == conversation


def test_compose_isolates_data_and_credentials():
    compose = yaml.safe_load((REPO / "docker/app/demo/compose.yaml").read_text())
    app, prepare = (compose["services"][name] for name in ("app", "prepare"))
    assert compose["name"] == "voc-local-demo"
    assert app["environment"]["VOC_AUTH_MODE"] == "local"
    assert app["environment"]["VOC_PLANNER_MODE"] == "model"
    assert app["ports"] == ["127.0.0.1:${VOC_DEMO_PORT:-8091}:8000"]
    assert app["volumes"] == ["catalog:/app/catalog:ro", "state:/app/state"]
    assert not any(
        key.startswith("AWS_") or "COGNITO" in key for key in app["environment"]
    )
    assert not any(
        "OPENROUTER" in key or "LLM" in key for key in prepare["environment"]
    )
    assert prepare["profiles"] == ["tools"]
    assert "depends_on" not in app
    assert "env_file" not in app and "env_file" not in prepare
    assert set(compose["volumes"]) == {"catalog", "state"}


@pytest.fixture
def launcher(tmp_path):
    repo = tmp_path / "repo with spaces"
    (repo / "scripts").mkdir(parents=True)
    (repo / "config").mkdir()
    for name in ("demo.sh", "demo.ps1"):
        shutil.copyfile(REPO / "scripts" / name, repo / "scripts" / name)
    private = repo / "config/demo.local.env"
    private.write_text("VOC_DEMO_OPENROUTER_API_KEY='synthetic-secret'\n")
    (repo / ".env").write_text("VOC_AUTH_MODE=cognito\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
args = sys.argv[1:]
with open(os.environ['DEMO_TEST_LOG'], 'a') as log:
    log.write(json.dumps({'args': args, 'stale': [k for k in os.environ if k.startswith(('VOC_DEMO_', 'COMPOSE_'))]})+'\\n')
if args[:1] == ['info']: print('linux')
if 'port' in args: print('127.0.0.1:8092')
if os.getenv('DEMO_TEST_FAIL') and os.getenv('DEMO_TEST_FAIL') in args: sys.exit(9)
"""
    )
    docker.chmod(0o755)
    log = tmp_path / "commands.jsonl"
    env = dict(
        os.environ,
        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
        DEMO_TEST_LOG=str(log),
        VOC_DEMO_PORT="9999",
        COMPOSE_FILE="wrong.yaml",
    )
    return repo, private, log, env


def run_launcher(launcher, shell, action):
    repo, _private, log, env = launcher
    if shell == "powershell":
        pwsh = os.environ.get("VOC_TEST_PWSH") or shutil.which("pwsh")
        if not pwsh:
            pytest.skip("PowerShell is needed to exercise the Windows launcher")
        # Stub only opening the browser. Execute the actual PS script/native argument passing.
        args = [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "function Start-Process {}\n& './scripts/demo.ps1' " + action,
        ]
    else:
        args = ["bash", "scripts/demo.sh", action]
    result = subprocess.run(
        args, check=False, cwd=repo, env=env, text=True, capture_output=True
    )
    calls = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    return result, calls


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_launchers_start_without_sync_and_preserve_private_files(launcher, shell):
    result, calls = run_launcher(launcher, shell, "up")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "http://localhost:8092" in result.stdout
    assert not any(call["stale"] for call in calls)
    args = [call["args"] for call in calls]
    assert any(
        a[-5:] == ["run", "--rm", "--no-deps", "prepare", "verify"] for a in args
    )
    assert not any("sync" in a or "build" in a for a in args)
    assert any("--wait" in a and "--wait-timeout" in a for a in args)
    assert launcher[1].read_text() == "VOC_DEMO_OPENROUTER_API_KEY='synthetic-secret'\n"
    assert (launcher[0] / ".env").read_text() == "VOC_AUTH_MODE=cognito\n"
    for a in args:
        if a[:1] == ["compose"] and "version" not in a:
            assert a[a.index("-p") + 1] == "voc-local-demo"
            assert a[a.index("--env-file") + 1] == str(launcher[1])


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_launchers_abort_before_start_on_failed_validation(launcher, shell):
    launcher[3]["DEMO_TEST_FAIL"] = "verify"
    result, calls = run_launcher(launcher, shell, "up")
    assert result.returncode != 0
    assert not any("up" in call["args"] for call in calls)


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_launchers_setup_and_stop_keep_volumes(launcher, shell):
    result, calls = run_launcher(launcher, shell, "setup")
    assert result.returncode == 0, result.stderr
    assert any(call["args"][-2:] == ["build", "app"] for call in calls)
    assert any(call["args"][-2:] == ["prepare", "sync"] for call in calls)
    result, calls = run_launcher(launcher, shell, "stop")
    assert result.returncode == 0, result.stderr
    assert calls[-1]["args"][-1] == "down"
    assert "--volumes" not in calls[-1]["args"] and "-v" not in calls[-1]["args"]
