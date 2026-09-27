#!/bin/bash
# macOS host setup. Re-running preserves configuration and installed tools.
set -euo pipefail
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'On Windows run scripts/setup.ps1 from PowerShell. This installer targets macOS.' >&2
  exit 1
fi
if ! command -v brew >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$candidate" ]]; then eval "$("$candidate" shellenv)"; break; fi
  done
fi
if ! command -v brew >/dev/null 2>&1; then
  echo 'Installing Homebrew using its official installer; follow its system prompts.'
  installer=$(mktemp)
  trap 'rm -f "$installer"' EXIT
  curl --fail --show-error --location https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$installer"
  /bin/bash "$installer"
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  else
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi
command -v just >/dev/null 2>&1 || brew install just
if [[ ! -d /Applications/Docker.app && ! -d "$HOME/Applications/Docker.app" ]]; then
  brew install --cask docker-desktop
fi
# Docker Desktop can be installed before its CLI is on PATH.
export PATH="/Applications/Docker.app/Contents/Resources/bin:$HOME/Applications/Docker.app/Contents/Resources/bin:$PATH"
open -a Docker
echo 'Finish Docker Desktop first-run prompts. Waiting up to three minutes for Linux containers...'
ready=false
for ((attempt=0; attempt<36; attempt++)); do
  if docker info >/dev/null 2>&1; then ready=true; break; fi
  sleep 5
done
if [[ "$ready" != true ]]; then
  echo 'Docker is not ready. Finish its setup, then rerun bash scripts/setup.sh.' >&2
  exit 1
fi
docker compose version >/dev/null
[[ "$(docker info --format '{{.OSType}}')" == linux ]] || { echo 'Select Linux containers in Docker Desktop.' >&2; exit 1; }
docker run --rm --mount "type=bind,source=$PWD,target=/workspace" \
  --workdir /workspace python:3.12-slim-bookworm \
  python scripts/configure.py --member "$(id -un)"
chmod 600 .env config/aws.local.env
# No secret values are printed; the teammate edits this private file locally.
open -e config/aws.local.env
printf '\nSetup complete. Save your AWS keys in the opened file, then run: just up\n'
if ! /bin/zsh -lc 'command -v just >/dev/null' 2>/dev/null; then
  echo 'If a new terminal cannot find just, follow Homebrew’s printed PATH instructions.'
fi
