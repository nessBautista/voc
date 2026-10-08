#!/bin/sh
# Run with a POSIX shell. Just loads root .env before calling this script.
# -e stops on unhandled command failures; -u rejects unset variables without defaults.
set -eu

# 1. Select the private file used by BOTH authentication modes.
# ${VARIABLE:-fallback} uses the fallback when the variable is unset or empty.
credential_file="${VOC_AWS_ENV_FILE:-config/aws.local.env}"
# test -r checks readability. If it fails, || runs the error block.
# >&2 sends the message to stderr; exit 1 reports failure and stops startup.
test -r "$credential_file" || { echo 'Create the credential file from config/aws.local.env.example.' >&2; exit 1; }

# 2. Remove inherited keys so old terminal credentials cannot override our selection.
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN OPENROUTER_API_KEY

# 3. Choose a branch, like switch/case. The default mode is env-file.
# The mode selects the reader; this script does not auto-detect op:// contents.
case "${VOC_AWS_AUTH_MODE:-env-file}" in
  env-file)
    # Read literal credentials. Root .env supplies settings; the second file supplies keys.
    # exec hands control to Compose; files are read as data, not executed as shell code.
    # up --build -d --wait builds, starts in the background, and waits for readiness.
    exec docker compose --env-file .env --env-file "$credential_file" -f compose.yaml up --build -d --wait
    # ;; ends this branch.
    ;;
  1password)
    # Check that op is installed; discard lookup output and show our message if missing.
    command -v op >/dev/null 2>&1 || { echo '1Password CLI is required for this selected mode.' >&2; exit 1; }
    # op run resolves secret references and gives Compose the resulting environment.
    # The -- separates op options from the command it should run.
    exec op run --env-file "$credential_file" -- docker compose -f compose.yaml up --build -d --wait
    ;;
  # * matches any unsupported mode, like a switch statement's default branch.
  *) echo 'Choose env-file or 1password in .env.' >&2; exit 1 ;;
# esac closes the case statement (case spelled backward).
esac
