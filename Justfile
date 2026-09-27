set dotenv-load
mode := env("VOC_STORAGE_MODE", "volume")
compose := if mode == "bind" { "docker compose -f compose.yaml -f compose.bind.yaml" } else { "docker compose -f compose.yaml" }

up:
    @test "{{mode}}" = volume -o "{{mode}}" = bind || { echo 'Use volume or bind'; exit 1; }
    mkdir -p data
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

# Host-only: resolve 1Password references, then forward credentials into Docker.
# `up` stays local; use `up-aws` again after restarting or refreshing credentials.
up-aws:
    @test "{{mode}}" = volume -o "{{mode}}" = bind || { echo 'Use volume or bind'; exit 1; }
    mkdir -p data
    op run --env-file "${VOC_AWS_REFS_FILE:-config/aws.refs.env}" -- sh scripts/up-aws.sh {{compose}} -f compose.aws-credentials.yaml

# Uses credentials already present in the running container; no host AWS alias.
s3-check:
    {{compose}} exec workspace python -m src.mlops.check_s3
