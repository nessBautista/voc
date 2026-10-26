#!/bin/sh
# Retained public startup entrypoint; workspace.sh owns all configuration.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
exec sh "$root/scripts/workspace.sh" up --build -d --wait "$@"
