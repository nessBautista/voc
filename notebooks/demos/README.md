# Topic modelling and sentiment demo

This directory is a standalone Marimo + Studio project. Its Python package,
Docker image, dependencies, tests and configuration live here. It does not import
`voc`, `voc_ml` or `voc_dev`, start ZenML/MLflow, or read the parent application's
configuration. You can copy this entire directory outside the repository.

## Start with Docker

Prerequisite: Docker with Compose (Docker Desktop on Windows, or OrbStack/Docker
Desktop on macOS). Run these commands from the repository root:

```bash
cd notebooks/demos
cp .env.example .env
```

In Windows PowerShell, use `Copy-Item .env.example .env` instead of `cp`.
Fill this demo's `.env` with the S3 bucket, region and your AWS credentials, then:

```bash
docker compose up --build -d --wait
```

For 1Password, put `op://` references in the key fields instead and launch with:

```bash
op run --env-file .env -- docker compose up --build -d --wait
```

Open the **Studio overview**:

<http://127.0.0.1:2730/studio/overview/?file=workflow01.py>

The notebook editor is at <http://127.0.0.1:2730/>. Change `DEMO_PORT` in `.env`
if needed. These commands manage the separate `voc-topic-demo` container. The
parent repository's `just up` continues to manage the main application.

```bash
docker compose logs --tail=50 demo
docker compose exec demo pytest -q
docker compose down
```

The first build installs ML dependencies and may take several minutes. Later
builds reuse the dependency layer unless this folder's dependency files change.
Both credentials and generated data are ignored by Git and excluded from builds.

## What loads

The demo pins dataset release `b7a61a8b-27d8-46a7-b2ce-67d97268b741` and demo
snapshot `188f1d136ccc01f9`. It reads the existing `voc/datasets/` and
`voc/demos/prototypeV0/` S3 formats, verifies downloaded checksums and seeds a
working copy with eleven saved result files. No snapshot files are bundled.

`prototypeV0` remains the S3 identifier. The notebook's `DEMO_RELEASE_ID` and
`DEMO_VERSION` select the published versions. Missing remote data stops loading.
A complete pinned download can subsequently load offline from a verified cache.

All runtime files stay in **this folder's `.data/`**: download caches, stage
results and model assets. They survive container recreation. The notebook still
runs calculations for its illustrations and may download model weights on first
use. Matching saved stage results are reused. Changes to parameters can trigger
recalculation.

OpenRouter is optional for viewing the published results. Set `OPENROUTER_API_KEY`
only to use the explicit generation button. To publish new results, also set
`DEMO_MEMBER_ID`, then use **Publish demo to S3**. That action writes a new complete
checkpoint and advances the shared demo pointer. It does not publish datasets.
Update the notebook's pins when deliberately distributing a new checkpoint.

## Layout

- `workflow01.py`: computation, teaching material and projected result objects.
- `__marimo__/studio/`: authored presentation view.
- `voc_demo/`: dataset/cache access, validation, topic interpretation and sentiment.
- `tests/`: synthetic, offline checks, including independence from VOC packages.
- `pyproject.toml`, `uv.lock`: independent Python environment.
- `Dockerfile`, `compose.yaml`, `.env.example`: independent launch configuration.

The S3 format helpers were adapted from the working prototype and are maintained
here for this demo. They share a data contract with VOC, not a Python dependency.

## Optional local Python environment

With Python 3.12 and uv installed, run from this directory:

```bash
uv sync --locked --group dev
uv run --env-file .env marimo edit workflow01.py --port 2730 --no-token
```

For 1Password, use `op run --env-file .env -- uv run marimo edit workflow01.py
--port 2730 --no-token` on one line. The environment and data directories remain
local to this folder. `DEMO_DATA_DIR` and `DEMO_MODEL_DIR` optionally override the
data and model-cache locations, respectively.
