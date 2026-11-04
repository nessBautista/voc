#!/usr/bin/env bash
# Exercise the complete local start/read/stop/restart cycle without model calls.
set -euo pipefail
DIFUSION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$DIFUSION_ROOT"
DIFUSION_SERVER_PID=''
stop_server() {
  if [[ -n "$DIFUSION_SERVER_PID" ]]; then
    kill -TERM "$DIFUSION_SERVER_PID" 2>/dev/null || true
    wait "$DIFUSION_SERVER_PID" || true
    DIFUSION_SERVER_PID=''
  fi
}
trap stop_server EXIT
# Do not take over or stop an already running app.
.venv/bin/python - <<'PY'
import socket
with socket.socket() as probe:
    probe.bind(('0.0.0.0', 8092))
PY
mkdir -p data/reports/difusion/demo
start_server() {
  bash scripts/difusion-demo.sh serve >> data/reports/difusion/demo/server.log 2>&1 &
  DIFUSION_SERVER_PID=$!
  .venv/bin/python - <<'PY'
import json, time, urllib.request
for _ in range(100):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8092/health/ready', timeout=1) as response:
            assert json.load(response)['status'] == 'ready'
        break
    except OSError:
        time.sleep(0.2)
else:
    raise RuntimeError('Demo did not become ready; see data/reports/difusion/demo/server.log')
PY
}
start_server
bash scripts/difusion.sh python scripts/check_difusion_demo.py
node apps/voc-app/web/scripts/check-difusion-demo.mjs
stop_server
.venv/bin/python - <<'PY'
import socket
with socket.socket() as probe:
    assert probe.connect_ex(('127.0.0.1',8092)) != 0
PY
start_server
.venv/bin/python - <<'PY'
import json, urllib.request
from datetime import datetime, timezone
from pathlib import Path
out=Path('data/reports/difusion/demo')
expected=json.loads((out/'api-verification.json').read_text())['alternative']['publication_id']
with urllib.request.urlopen('http://127.0.0.1:8092/api/v1/publications/current') as response:
    assert json.load(response)['publication_id'] == expected
(out/'lifecycle-verification.json').write_text(json.dumps({
    'observed_at':datetime.now(timezone.utc).isoformat(),
    'startup':True,'shutdown':True,'selection_survived_restart':True,
    'publication_id':expected,'model_requests':0},indent=2)+'\n')
print('Start, read, stop and restart verified. The test server will now stop.')
PY
