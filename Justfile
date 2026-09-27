set dotenv-load
mode := env("VOC_STORAGE_MODE", "volume")
base_compose := if mode == "bind" { "docker compose -f compose.yaml -f compose.bind.yaml" } else { "docker compose -f compose.yaml" }
# Only publishers with a configured seed directory receive the seed mount.
seed_compose := if env("VOC_SEED_DIR", "") == "" { "" } else { " -f compose.seed.yaml" }
compose := base_compose + seed_compose

# Default team startup: resolve AWS credentials before creating the container.
up: up-aws

# Explicit fully local mode, without AWS credential injection.
up-local:
    @test "{{mode}}" = volume -o "{{mode}}" = bind || { echo 'Use volume or bind'; exit 1; }
    mkdir -p data
    VOC_ARTIFACT_DESTINATION=local VOC_DATASET_SOURCE=local {{compose}} up --build -d --wait

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

# Host-only: select 1Password or a local credential file; both use runtime injection.
# `up` delegates here; `up-aws` remains available for existing instructions.
up-aws:
    @test "{{mode}}" = volume -o "{{mode}}" = bind || { echo 'Use volume or bind'; exit 1; }
    mkdir -p data
    sh scripts/start-aws.sh {{compose}}

# Uses credentials already present in the running container; no host AWS alias.
s3-check:
    {{compose}} exec workspace python -m src.mlops.check_s3
