#!/bin/sh
# Runs inside `op run` on the HOST, before Docker receives the environment.
set -eu
: "${AWS_ACCESS_KEY_ID:?1Password must supply AWS_ACCESS_KEY_ID}"
: "${AWS_SECRET_ACCESS_KEY:?1Password must supply AWS_SECRET_ACCESS_KEY}"
: "${AWS_REGION:?Set AWS_REGION to a region code, such as us-east-2}"

# Reject unresolved references without printing any credential values.
case "$AWS_ACCESS_KEY_ID $AWS_SECRET_ACCESS_KEY ${AWS_SESSION_TOKEN:-}" in
  *op://*) echo 'Unresolved 1Password reference; check your reference file.' >&2; exit 1 ;;
esac

# Arguments are the Compose command and files selected by the Justfile.
# Credentials are runtime environment values, never Docker build arguments.
exec "$@" up --build -d --wait
