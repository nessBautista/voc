#!/bin/sh
# HOST launcher: never source credential files as executable shell code.
set -eu
case "${VOC_AWS_AUTH_MODE:-1password}" in
  1password)
    command -v op >/dev/null 2>&1 || {
      echo 'Install 1Password CLI or set VOC_AWS_AUTH_MODE=env-file in .env.' >&2
      exit 1
    }
    exec op run --env-file "${VOC_AWS_REFS_FILE:-config/aws.refs.env}" -- \
      sh scripts/up-aws.sh "$@" -f compose.aws-credentials.yaml
    ;;
  env-file)
    credential_file="${VOC_AWS_ENV_FILE:-config/aws.local.env}"
    test -f "$credential_file" && test -r "$credential_file" || {
      echo 'Create the ignored AWS credential file from config/aws.local.env.example.' >&2
      exit 1
    }
    # Compose gives exported variables precedence over --env-file. Clear AWS
    # inputs so a teammate does not accidentally inherit another terminal's identity.
    # Just has already loaded/exported .env settings (ports, mounts, login token).
    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
    unset AWS_REGION AWS_DEFAULT_REGION AWS_BUCKET VOC_MEMBER_ID VOC_SMOKE_KEY
    unset VOC_ARTIFACT_DESTINATION VOC_DATASET_SOURCE
    exec "$@" --env-file "$credential_file" -f compose.aws-credentials.yaml \
      up --build -d --wait
    ;;
  *)
    echo 'VOC_AWS_AUTH_MODE must be 1password or env-file.' >&2
    exit 1
    ;;
esac
