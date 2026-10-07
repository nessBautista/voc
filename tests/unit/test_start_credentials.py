"""Exercise both host startup branches with fake commands, never real credentials."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX startup script")
@pytest.mark.parametrize("mode", ["env-file", "1password"])
def test_optional_llm_credential_uses_selected_file(tmp_path, mode):
    private = tmp_path / "credentials.local.env"
    private.write_text("OPENROUTER_API_KEY=''\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Capture arguments and only whether inherited keys survived, not their values.
    command = '#!/bin/sh\n' + '''printf '%s\\n' "$@" > "$CAPTURE"
if [ "${OPENROUTER_API_KEY+x}" = x ]; then exit 71; fi
if [ "${AWS_ACCESS_KEY_ID+x}" = x ]; then exit 72; fi
'''
    for name in ("docker", "op"):
        path = bin_dir / name
        path.write_text(command)
        path.chmod(0o755)
    capture = tmp_path / "args"
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
               VOC_AWS_AUTH_MODE=mode, VOC_AWS_ENV_FILE=str(private), CAPTURE=str(capture),
               OPENROUTER_API_KEY="synthetic-stale-key", AWS_ACCESS_KEY_ID="synthetic-stale-aws")
    subprocess.run(["sh", str(ROOT / "scripts/start.sh")], env=env, cwd=tmp_path, check=True)
    args = capture.read_text().splitlines()
    if mode == "env-file":
        assert args[:5] == ["compose", "--env-file", ".env", "--env-file", str(private)]
    else:
        assert args[:6] == ["run", "--env-file", str(private), "--", "docker", "compose"]
    assert args[-4:] == ["up", "--build", "-d", "--wait"]
