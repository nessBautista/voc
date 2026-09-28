set dotenv-load
compose := "docker compose -f compose.yaml"

up:
    mkdir -p data
    {{compose}} up --build -d --wait

down:
    {{compose}} down

shell:
    @if [ -n "${MSYSTEM:-}" ] && command -v winpty >/dev/null 2>&1; then winpty {{compose}} exec workspace bash; else {{compose}} exec workspace bash; fi

status:
    {{compose}} ps

test:
    {{compose}} exec workspace pytest -q
