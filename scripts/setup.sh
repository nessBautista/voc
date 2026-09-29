#!/bin/bash
# macOS project setup. Host tools must already be installed and running.
set -euo pipefail
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'On Windows run scripts/setup.ps1 from PowerShell. This setup script targets macOS.' >&2
  exit 1
fi
for tool in git just docker; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing prerequisite: $tool. Install it separately and reopen your terminal before rerunning setup." >&2
    exit 1
  fi
done
# Use the active Docker context, including OrbStack. Never install, open, or switch runtimes.
if ! docker info >/dev/null 2>&1; then
  echo 'Container runtime is not ready. Start OrbStack or your existing Docker runtime, then rerun setup.' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo 'Docker Compose v2 is required. Complete your runtime setup separately, then rerun.' >&2
  exit 1
fi
[[ "$(docker info --format '{{.OSType}}')" == linux ]] || { echo 'Use a Linux-container runtime before running setup.' >&2; exit 1; }
# Docker may download this Python image; no host software is installed.
docker run --rm --mount "type=bind,source=$PWD,target=/workspace" \
  --workdir /workspace python:3.12-slim-bookworm \
  python scripts/configure.py --member "$(id -un)"
chmod 600 .env config/aws.local.env
# No secret values are printed; the teammate edits this private file locally.
if ! open -e config/aws.local.env; then
  echo 'Open config/aws.local.env in your editor to fill your AWS keys.'
fi
printf '\nSetup complete. Save your AWS keys in config/aws.local.env, then run: just up\n'
