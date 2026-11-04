#!/usr/bin/env bash
# Run the diffusion checkout with independent local state; never source demo .env.
set -euo pipefail
DIFUSION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ ! -x "$DIFUSION_ROOT/.venv/bin/python" ]]; then
  echo 'Install docker/operator/uv.lock into .venv first; see docs/difusion-workspace.md.' >&2
  exit 1
fi
# Do not inherit another installation's store, experiment, endpoint or demo paths.
for variable in ${!ZENML_@} ${!MLFLOW_@} ${!DEMO_@} ${!VOC_@}; do
  unset "$variable"
done
unset PYTHONPATH PYTHONHOME AWS_BUCKET
export VIRTUAL_ENV="$DIFUSION_ROOT/.venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export VOC_DATA_DIR="$DIFUSION_ROOT/data"
export VOC_STORAGE_CONFIG="$DIFUSION_ROOT/config/storage-difusion.toml"
export VOC_ARTIFACT_DESTINATION=local VOC_DATASET_SOURCE=personal
export VOC_MEMBER_ID=difusion VOC_ANALYSIS_READ_DIRS="$VOC_DATA_DIR"
export ZENML_CONFIG_PATH="$VOC_DATA_DIR/zenml-client"
export ZENML_CUSTOM_SOURCE_ROOT="$DIFUSION_ROOT"
export ZENML_ANALYTICS_OPT_IN=false
export MLFLOW_TRACKING_URI="sqlite:///$VOC_DATA_DIR/mlflow/mlflow.db"
export MLFLOW_REGISTRY_URI="$MLFLOW_TRACKING_URI"
export MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ENABLE_TRACING=false
export MLFLOW_ENABLE_TELEMETRY=false
export HF_HOME="$VOC_DATA_DIR/cache/huggingface"
# Argo persists this private cache across container recreation. Keep the secret
# outside the checkout and pass only its path, never its value, to child processes.
unset HF_TOKEN HUGGING_FACE_HUB_TOKEN
export HF_TOKEN_PATH="$HOME/.cache/voc-difusion/hf-token"
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_CACHE="$HF_HOME/hub" HF_ASSETS_CACHE="$HF_HOME/assets"
export SENTENCE_TRANSFORMERS_HOME="$VOC_DATA_DIR/models/sentence-transformers"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export DEMO_DATA_DIR="$VOC_DATA_DIR" DEMO_MODEL_DIR="$VOC_DATA_DIR/models"
export XDG_CACHE_HOME="$VOC_DATA_DIR/cache/xdg"
export MPLCONFIGDIR="$VOC_DATA_DIR/cache/matplotlib"
export NUMBA_CACHE_DIR="$VOC_DATA_DIR/cache/numba"
export TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
mkdir -p "$VOC_DATA_DIR/mlflow" "$XDG_CACHE_HOME" "$MPLCONFIGDIR" "$NUMBA_CACHE_DIR"
cd "$DIFUSION_ROOT"
if [[ $# == 0 ]]; then
  echo 'Usage: bash scripts/difusion.sh <command> [args...]' >&2
  exit 2
fi
exec "$@"
