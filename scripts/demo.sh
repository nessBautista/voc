#!/usr/bin/env bash
# Local web demo on macOS/Linux. Windows teammates use demo.ps1.
set -euo pipefail
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
action="${1:-up}"
case "$action" in configure|setup|sync|up|stop|status|logs) ;; *) echo 'Usage: bash scripts/demo.sh {configure|setup|sync|up|stop|status|logs}' >&2; exit 2;; esac
# File values win over other installations' shell settings (this child shell only).
for variable in ${!VOC_DEMO_@} ${!COMPOSE_@}; do unset "$variable"; done
private_file="$PWD/config/demo.local.env"
if [[ "$action" == configure ]]; then
  if [[ ! -f "$private_file" ]]; then
    (umask 077; cp config/demo.local.env.example "$private_file")
  fi
  echo 'Edita config/demo.local.env, guarda tus llaves y ejecuta: bash scripts/demo.sh setup'
  exit 0
fi
[[ -f "$private_file" ]] || { echo 'Primero ejecuta: bash scripts/demo.sh configure' >&2; exit 1; }
command -v docker >/dev/null || { echo 'Instala e inicia Docker con contenedores Linux.' >&2; exit 1; }
docker compose version
[[ "$(docker info --format '{{.OSType}}')" == linux ]] || { echo 'Inicia Docker con contenedores Linux.' >&2; exit 1; }
compose() {
  docker compose --project-directory "$PWD" --env-file "$private_file" \
    -p voc-local-demo -f docker/app/demo/compose.yaml "$@"
}
case "$action" in
  setup)
    compose build app
    compose run --rm --no-deps prepare sync
    echo 'Preparacion completa. Ejecuta: bash scripts/demo.sh up'
    ;;
  sync) compose run --rm --no-deps prepare sync ;;
  up)
    compose run --rm --no-deps prepare verify
    compose run --rm --no-deps --entrypoint python app -c "import sys; from voc_app.settings import Settings; s=Settings.from_env(); sys.exit(0 if s.llm_api_key.strip() and not s.llm_api_key.startswith('op://') else 'Completa VOC_DEMO_OPENROUTER_API_KEY en config/demo.local.env')"
    compose up --detach --wait --wait-timeout 180 app
    binding="$(compose port app 8000)"
    port="${binding##*:}"
    echo "Chat: http://localhost:$port/  |  Dashboard: http://localhost:$port/dashboard/"
    ;;
  stop) compose down ;;
  status) compose ps -a ;;
  logs) compose logs --tail 80 app ;;
esac
