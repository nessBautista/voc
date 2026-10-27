#!/bin/sh
# Run on the Mac. Keep Studio's environment and threads out of the demo app.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
command -v uv >/dev/null 2>&1 || {
  echo 'Install uv on the Mac first: https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
}
port=${VOC_STUDIO_PORT:-2024}
case "$port" in ''|*[!0-9]*) echo 'VOC_STUDIO_PORT must be an integer.' >&2; exit 1 ;; esac
if [ "$port" -lt 1024 ] || [ "$port" -gt 65535 ] || [ "$port" -eq 8091 ]; then
  echo 'Choose a Studio port between 1024 and 65535, other than demo port 8091.' >&2
  exit 1
fi
cd "$repo/tools/lang-studio"
set -- uv run --project . --locked
if [ -f "$repo/config/lang-studio.local.env" ]; then
  set -- "$@" --env-file "$repo/config/lang-studio.local.env"
fi
# Studio is local by default, even if the caller enabled tracing for another tool.
export LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false
export LANGGRAPH_CLI_NO_ANALYTICS=1
export BG_JOB_TIMEOUT_SECS=90
"$@" python prepare.py
printf 'Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:%s\n' "$port"
printf 'Keep this terminal open; Ctrl+C stops Studio. The AWS demo is unchanged.\n'
printf 'If Chrome cannot connect, allow Loopback network (or Local network access) for smith.langchain.com and reload.\n'
if [ "${VOC_NO_BROWSER:-0}" = 1 ]; then
  exec "$@" langgraph dev --config langgraph.json --host 127.0.0.1 --port "$port" --no-browser
fi
# Safari blocks the hosted Studio UI from connecting to localhost. Prefer Chrome on Mac.
if [ "$(uname -s)" = Darwin ] && [ -z "${BROWSER:-}" ] && open -Ra "Google Chrome" >/dev/null 2>&1; then
  export BROWSER='open -a "Google Chrome" %s'
fi
exec "$@" langgraph dev --config langgraph.json --host 127.0.0.1 --port "$port"
