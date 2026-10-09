set dotenv-load
compose := "docker compose -f compose.yaml"

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
