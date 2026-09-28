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
