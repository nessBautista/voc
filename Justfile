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

# The end-user application has its own project and no Operator credential loader.
app-up:
    mkdir -p data/voc-app/catalog data/voc-app/state
    {{app_compose}} up --build -d --wait

app-status:
    {{app_compose}} ps

app-stop:
    {{app_compose}} down

app-logs:
    {{app_compose}} logs --tail 80 app
