#!/usr/bin/env bash
# Reuse the original web app with an independent local catalog and state.
set -euo pipefail
DIFUSION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ ${1:-} == serve ]]; then
  exec bash "$DIFUSION_ROOT/scripts/difusion.sh" env \
    VOC_PUBLICATIONS_DIR="$DIFUSION_ROOT/data/difusion-app/catalog" \
    VOC_APP_STATE_DIR="$DIFUSION_ROOT/data/difusion-app/state" \
    VOC_WEB_DIR="$DIFUSION_ROOT/apps/voc-app/web/dist" \
    VOC_AUTH_MODE=local VOC_PLANNER_MODE=fixture VOC_MCP_ENABLED=false \
    VOC_PUBLIC_ORIGIN=http://localhost:8092 \
    "$DIFUSION_ROOT/data/venvs/app/bin/python" -m uvicorn voc_app.main:app \
    --host 0.0.0.0 --port 8092
fi
exec bash "$DIFUSION_ROOT/scripts/difusion.sh" python "$DIFUSION_ROOT/scripts/difusion_demo.py" "$@"
