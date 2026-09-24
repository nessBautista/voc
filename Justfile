set dotenv-load
mode := env("VOC_STORAGE_MODE", "volume")
compose := if mode == "bind" { "docker compose -f compose.yaml -f compose.bind.yaml" } else { "docker compose -f compose.yaml" }

up:
    @test "{{mode}}" = volume -o "{{mode}}" = bind || { echo 'Use volume or bind'; exit 1; }
    mkdir -p data/runtime
    {{compose}} up --build -d --wait

down:
    {{compose}} down

shell:
    {{compose}} exec workspace bash

logs:
    {{compose}} exec workspace sh -c 'tail -n 60 /data/logs/*.log'

status:
    {{compose}} ps

test:
    {{compose}} exec workspace pytest -q
