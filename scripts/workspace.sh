#!/bin/sh
# All host Compose entrypoints share this validated, repository-pinned bridge.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
export MSYS_NO_PATHCONV=1
host_root=$root
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) host_root=$(cygpath -m "$root") ;;
esac
command=${1:-}
[ -n "$command" ] || { echo 'Usage: workspace.sh <compose-command|notebook|shell> [arguments]' >&2; exit 1; }
shift
# Do not pass the host environment into the disposable configuration reader.
unset JUPYTER_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_SECURITY_TOKEN AWS_PROFILE AWS_DEFAULT_PROFILE OPENROUTER_API_KEY
unset AWS_REGION AWS_DEFAULT_REGION AWS_BUCKET VOC_MEMBER_ID VOC_EMBEDDINGS_PREFIX VOC_DATASET_SOURCE VOC_ARTIFACT_DESTINATION VOC_STORAGE_CONFIG VOC_STORAGE_MODE
unset VOC_AWS_AUTH_MODE VOC_AWS_ENV_FILE COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_ENV_FILES COMPOSE_PROFILES
unset VOC_ZENML_PORT VOC_MLFLOW_PORT VOC_JUPYTER_PORT VOC_MARIMO_PORT
if [ "$command" = up ]; then
  records=$(docker run --rm --network none --mount "type=bind,source=$host_root,target=/workspace,readonly" --workdir /workspace python:3.12-slim-bookworm python scripts/workspace.py --root /workspace --credentials)
else
  records=$(docker run --rm --network none --mount "type=bind,source=$host_root,target=/workspace,readonly" --workdir /workspace python:3.12-slim-bookworm python scripts/workspace.py --root /workspace)
fi
# Treat helper output as records, never as executable shell text.
while IFS='=' read -r key value; do
  case "$key" in
    VOC_WORKSPACE_AUTH_MODE|VOC_WORKSPACE_CREDENTIAL_FILE|VOC_ZENML_PORT|VOC_MLFLOW_PORT|VOC_JUPYTER_PORT|VOC_MARIMO_PORT|AWS_REGION|AWS_DEFAULT_REGION) export "$key=$value" ;;
    *) echo 'Unexpected workspace configuration record.' >&2; exit 1 ;;
  esac
done <<EOF
$records
EOF
# Prevent compose-global options from replacing the pinned repository/project.
case "$command" in
  -*|'') echo 'Pass a Compose subcommand, not global options.' >&2; exit 1 ;;
esac
if [ "$command" = up ]; then
  credential_file=$host_root/$VOC_WORKSPACE_CREDENTIAL_FILE
  case "$VOC_WORKSPACE_AUTH_MODE" in
    env-file)
      exec docker compose --project-directory "$host_root" --env-file "$host_root/.env" --env-file "$credential_file" -f "$host_root/compose.yaml" -p voc-main up "$@" ;;
    1password)
      command -v op >/dev/null 2>&1 || { echo '1Password CLI is required for this selected mode.' >&2; exit 1; }
      exec op run --env-file "$credential_file" -- docker compose --project-directory "$host_root" --env-file "$host_root/.env" -f "$host_root/compose.yaml" -p voc-main up "$@" ;;
  esac
fi
if [ "$command" = notebook ]; then
  kind=${1:-}
  operation=${2:-open}
  case "$kind" in
    marimo) port=$VOC_MARIMO_PORT ;;
    jupyter) port=$VOC_JUPYTER_PORT ;;
    *) echo 'Choose marimo or jupyter.' >&2; exit 1 ;;
  esac
  exec sh "$root/scripts/notebook.sh" "$kind" "$operation" "$port" docker compose --project-directory "$host_root" --env-file "$host_root/.env" -f "$host_root/compose.yaml" -p voc-main
fi
if [ "$command" = shell ]; then
  if [ -n "${MSYSTEM:-}" ] && command -v winpty >/dev/null 2>&1; then
    exec winpty docker compose --project-directory "$host_root" --env-file "$host_root/.env" -f "$host_root/compose.yaml" -p voc-main exec workspace bash "$@"
  fi
  command=exec
  set -- workspace bash "$@"
fi
exec docker compose --project-directory "$host_root" --env-file "$host_root/.env" -f "$host_root/compose.yaml" -p voc-main "$command" "$@"
