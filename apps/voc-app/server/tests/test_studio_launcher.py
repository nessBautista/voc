"""The Studio command must remain local and stop if preflight fails."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("no_browser", ["0", "1"])
def test_launcher_uses_private_settings_and_forces_local_tracing_off(
    tmp_path, no_browser
):
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "tools/lang-studio").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config/lang-studio.local.env").write_text(
        "VOC_STUDIO_PLANNER_MODE=fixture\n"
    )
    script = root / "scripts/lang-studio.sh"
    shutil.copyfile(REPO / "scripts/lang-studio.sh", script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "uv"
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
print(json.dumps({'args':sys.argv[1:], 'tracing':os.environ.get('LANGSMITH_TRACING'),
'legacy_tracing':os.environ.get('LANGCHAIN_TRACING_V2'), 'cwd':os.getcwd(),
'limit':os.environ.get('BG_JOB_TIMEOUT_SECS')}))
""")
    fake.chmod(0o755)
    env = {
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "VOC_NO_BROWSER": no_browser,
        "VOC_STUDIO_PORT": "2025",
    }
    r = subprocess.run(
        ["sh", str(script)], env=env, capture_output=True, text=True, check=True
    )
    calls = [json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")]
    assert len(calls) == 2
    assert calls[0]["args"][-2:] == ["python", "prepare.py"]
    server = calls[1]
    args = server["args"]
    assert "--locked" in args
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "2025"
    assert args[args.index("--env-file") + 1] == str(
        root / "config/lang-studio.local.env"
    )
    assert ("--no-browser" in args) == (no_browser == "1")
    assert all(c["tracing"] == c["legacy_tracing"] == "false" for c in calls)
    assert server["limit"] == "90"
    assert "--tunnel" not in args


def test_failed_preflight_does_not_launch_server(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "uv"
    fake.write_text("#!/bin/sh\nprintf 'preflight attempted\\n'\nexit 23\n")
    fake.chmod(0o755)
    r = subprocess.run(
        ["sh", str(REPO / "scripts/lang-studio.sh")],
        env={"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 23
    assert r.stdout.count("preflight attempted") == 1
    assert "Studio: https://" not in r.stdout


def test_demo_port_is_rejected_before_startup():
    r = subprocess.run(
        ["sh", str(REPO / "scripts/lang-studio.sh")],
        env={**os.environ, "VOC_STUDIO_PORT": "8091"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0
    assert "8091" in r.stderr
