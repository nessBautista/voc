#!/bin/sh
# Host browser launcher. The running container creates files in its repo mount.
set -eu
kind=$1
operation=$2
port=$3
shift 3
# Git Bash must not translate container arguments such as /workspace into C: paths.
export MSYS_NO_PATHCONV=1
if [ "$operation" = create ]; then
  url=$("$@" exec -T workspace python -m voc_dev.notebooks "$kind" --port "$port" --name "$notebook_name")
elif [ "$operation" = reports ]; then
  url=$("$@" exec -T workspace python -m voc_dev.notebooks "$kind" --port "$port" --reports)
else
  url=$("$@" exec -T workspace python -m voc_dev.notebooks "$kind" --port "$port")
fi
case "$url" in
  http://127.0.0.1:*) ;;
  *) echo 'Could not obtain the notebook URL. Start the container with just up.' >&2; exit 1 ;;
esac
# Display a usable link without leaking the login token to terminal logs.
public_url=${url%%\?*}
if [ "$kind" = marimo ] && [ "$operation" = create ]; then
  public_url=${url%%&access_token=*}
fi
printf 'Notebook editor: %s\n' "$public_url"
if [ "${VOC_NO_BROWSER:-0}" = 1 ]; then exit 0; fi
case "$(uname -s)" in
  Darwin) open "$url" && exit 0 ;;
  MINGW*|MSYS*|CYGWIN*)
    VOC_BROWSER_URL="$url" powershell.exe -NoProfile -NonInteractive \
      -Command 'Start-Process $env:VOC_BROWSER_URL' && exit 0 ;;
  *) command -v xdg-open >/dev/null 2>&1 && xdg-open "$url" >/dev/null 2>&1 && exit 0 ;;
esac
printf 'Browser did not open automatically. Open the link above; your login token is in .env.\n'
