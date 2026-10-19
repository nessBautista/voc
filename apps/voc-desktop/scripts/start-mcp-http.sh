#!/usr/bin/env bash
# Keep the token out of argv, output and repository files.
set -euo pipefail
if [[ -z "${VOC_DESKTOP_MCP_TOKEN:-}" ]]; then
    VOC_DESKTOP_MCP_TOKEN="$(op read 'op://Development/voc-desktop-mcp-token/password')"
    export VOC_DESKTOP_MCP_TOKEN
fi
exec "$1" --http
