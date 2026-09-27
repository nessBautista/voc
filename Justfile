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
    @if [ -n "${MSYSTEM:-}" ] && command -v winpty >/dev/null 2>&1; then winpty {{compose}} exec workspace bash; else {{compose}} exec workspace bash; fi

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

# Create/open a .py notebook; exported argument avoids interpolating user input as shell code.
new-marimo $notebook_name:
    @sh scripts/notebook.sh marimo create {{env("VOC_MARIMO_PORT", "2718")}} {{compose}}

# Create/open a Jupyter .ipynb notebook under the same notebooks/ directory.
new-notebook $notebook_name:
    @sh scripts/notebook.sh jupyter create {{env("VOC_JUPYTER_PORT", "8888")}} {{compose}}

# Open either editor's home page using the running container's login token.
marimo:
    @sh scripts/notebook.sh marimo open {{env("VOC_MARIMO_PORT", "2718")}} {{compose}}

jupyter:
    @sh scripts/notebook.sh jupyter open {{env("VOC_JUPYTER_PORT", "8888")}} {{compose}}
