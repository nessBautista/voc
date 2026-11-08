#!/usr/bin/env bash
# Launch the diffusion reports in its own Python environment, inside Argo.
set -euo pipefail
DIFUSION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
kind="${1:-}"
case "$kind" in marimo|jupyter) ;; *) echo 'Uso: difusion-notebooks.sh marimo|jupyter' >&2; exit 2 ;; esac

# On the Mac, use the already running Argo container (not VOC's demo Compose).
if [[ "${ARGO_CONTAINER:-}" != 1 && "${2:-}" != --inside-argo ]]; then
  argo_root="$DIFUSION_ROOT"
  while [[ ! -f "$argo_root/ops/docker/compose.yaml" && "$argo_root" != / ]]; do
    argo_root="$(dirname "$argo_root")"
  done
  if [[ ! -f "$argo_root/ops/docker/compose.yaml" ]]; then
    echo 'No se encontró la instalación de Argo. Ejecuta esta acción dentro de Argo, desde el checkout voc-difusion.' >&2
    exit 2
  fi
  command -v docker >/dev/null 2>&1 || { echo 'Docker no está disponible. Abre una terminal del contenedor Argo y ejecuta allí la acción.' >&2; exit 2; }
  compose=(docker compose --file "$argo_root/ops/docker/compose.yaml")
  if [[ -f "$argo_root/ops/docker/.env" ]]; then
    compose+=(--env-file "$argo_root/ops/docker/.env")
  else
    compose+=(--env-file "$argo_root/ops/docker/.env.example")
  fi
  repo_relative="${DIFUSION_ROOT#"$argo_root"/}"
  echo 'Usando el contenedor Argo existente. Si está apagado, inicia Argo con just up.' >&2
  exec_flags=(exec)
  # A TTY forwards Ctrl+C to the foreground server in interactive host shells.
  if [[ ! -t 0 || ! -t 1 ]]; then exec_flags+=(-T); fi
  exec "${compose[@]}" "${exec_flags[@]}" --workdir "/workspace/$repo_relative" workspace \
    bash scripts/difusion-notebooks.sh "$kind" --inside-argo
fi
exec bash "$DIFUSION_ROOT/scripts/difusion.sh" python "$DIFUSION_ROOT/scripts/difusion_notebooks.py" "$kind"
