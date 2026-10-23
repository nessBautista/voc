set dotenv-load
app_compose := "docker compose --project-directory . -p voc-state-app -f docker/app/compose.yaml"
compose := "docker compose --project-directory . --env-file .env -f docker/operator/compose.yaml"

# Load the selected credentials before Compose creates the container.
up:
    mkdir -p data
    sh scripts/start.sh

down:
    {{compose}} down

shell:
    @if [ -n "${MSYSTEM:-}" ] && command -v winpty >/dev/null 2>&1; then winpty {{compose}} exec workspace bash; else {{compose}} exec workspace bash; fi

status:
    {{compose}} ps

test:
    {{compose}} exec workspace pytest -q

# The app server keeps its own lock; run its suite inside the Operator container,
# with a separate environment under data/ so /opt/venv stays the Operator's.
server-test:
    {{compose}} exec -e UV_PROJECT_ENVIRONMENT=/workspace/data/venvs/voc-app-server -e UV_CACHE_DIR=/workspace/data/venvs/uv-cache workspace uv run --project apps/voc-app/server --group dev --locked pytest -q apps/voc-app/server/tests

# Web unit tests and the production build in a throwaway Node container; nothing installs on the host.
web-test:
    docker run --rm -v "{{justfile_directory()}}/apps/voc-app/web:/web" -w /web node:22-bookworm-slim sh -c "npm ci --no-fund --no-audit && npm test && npm run build"

# The browser suite in Microsoft's Playwright image, which ships the pinned Chromium; routes are stubbed, no server needed.
web-browser-test:
    docker run --rm --ipc=host -v "{{justfile_directory()}}/apps/voc-app/web:/web" -w /web mcr.microsoft.com/playwright:v1.64.1-noble sh -c "npm ci --no-fund --no-audit && npm run test:browser"

new-marimo $notebook_name:
    @sh scripts/notebook.sh marimo create {{env("VOC_MARIMO_PORT", "2721")}} {{compose}}

new-notebook $notebook_name:
    @sh scripts/notebook.sh jupyter create {{env("VOC_JUPYTER_PORT", "8890")}} {{compose}}

marimo:
    @sh scripts/notebook.sh marimo open {{env("VOC_MARIMO_PORT", "2721")}} {{compose}}

jupyter:
    @sh scripts/notebook.sh jupyter open {{env("VOC_JUPYTER_PORT", "8890")}} {{compose}}

jupyter-reports:
    @sh scripts/notebook.sh jupyter reports {{env("VOC_JUPYTER_PORT", "8890")}} {{compose}}

logs:
    {{compose}} exec workspace sh -c 'tail -n 60 /workspace/data/logs/*.log'

# The application only needs the shared credential loader in model mode.
app-up:
    mkdir -p data/voc-app/catalog data/voc-app/state
    sh scripts/start.sh app

app-status:
    {{app_compose}} ps

app-stop:
    {{app_compose}} down

app-logs:
    {{app_compose}} logs --tail 80 app

# Exercise the built app with isolated synthetic publications; run on the Mac.
app-cycle action:
    python3 tests/acceptance/data_state_cycle/cycle.py {{quote(action)}}
