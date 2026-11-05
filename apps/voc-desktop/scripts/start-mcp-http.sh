#!/usr/bin/env bash
# Keep the token out of argv, output and repository files.
set -euo pipefail
if [[ $# -ne 1 ]]; then
    echo "Usage: start-mcp-http.sh <desktop-binary>" >&2
    exit 2
fi
# Set the private reference in the calling shell, never in this tracked script.
if [[ -z "${VOC_DESKTOP_MCP_TOKEN:-}" ]]; then
    : "${VOC_DESKTOP_MCP_TOKEN_REF:?Set a private reference or VOC_DESKTOP_MCP_TOKEN outside the repository}"
    VOC_DESKTOP_MCP_TOKEN="$(op read "$VOC_DESKTOP_MCP_TOKEN_REF")"
    export VOC_DESKTOP_MCP_TOKEN
fi
exec "$1" --http
