hola

## Start here for the team session

Use the team setup guide in the Sonar vault (`journal/linear/WOR-200/team-session.md`) to fetch
`WOR-200-MLOps-infra`, run the Windows/macOS project setup, fill your AWS keys,
and open your workspace. The setup scripts preserve existing settings.

```bash
just up
just new-marimo teammate/my-notebook
# Or create a Jupyter notebook:
just new-notebook teammate/my-notebook
```

`just marimo` and `just jupyter` open the editor home pages. Notebook commands
run on the host while the container is running, save under `notebooks/`, and
open existing files without replacing them. They respect your configured ports.
The guide includes facilitator checkpoints and the later move back to `main`.

## Fresh teammate setup (without 1Password)

Use branch `WOR-200-MLOps-infra` while this setup is under review. Install Docker
Desktop on Windows or OrbStack on macOS (with Compose v2), Git and `just` on the
host. The setup scripts check these prerequisites; they do not install host tools
or switch container runtimes. Python, uv, AWS SDKs and the notebook tools are
installed inside the image. This path needs neither the AWS
CLI nor `op`. On Windows, use Git Bash for the current shell recipes; Windows
execution is still unverified.

From a fresh checkout, copy these templates:

```bash
cp .env.example .env
cp config/aws.local.env.example config/aws.local.env
```

Set a private `JUPYTER_TOKEN` in `.env`; both notebook editors use it. In
`config/aws.local.env`, enter your supplied AWS key ID, secret key, bucket, region
code, and your own `VOC_MEMBER_ID` (letters, digits, underscore or hyphen).
Include a session token only for temporary credentials. Keep both storage
selections as `s3`. Use single-quoted values as shown in the template.

These copies are ignored by Git and Docker builds. The local credential file is
plaintext: restrict its file permissions/access to your account (on macOS/Linux,
`chmod 600 .env config/aws.local.env`). It remains visible inside the container
through the repo bind mount, and injected credentials appear in Docker metadata.
Do not share these files or expanded environment/Compose output. Edit only the
copies, never put secrets in the tracked examples.

```bash
just up
just shell
```

Inside the container:

```bash
voc-ml storage-info
voc dataset-info
python -m src.mlops.check_shared_reader
```

Expect `artifact_destination: s3`, `dataset_source: s3`, your member prefix with
a newly generated installation ID, and a published shared release. No seed,
`dataset init`, collector update, or dataset pipeline run is needed for reading.
Open Jupyter at <http://127.0.0.1:8888> or Marimo at <http://127.0.0.1:2719> (the
`.env.example` host port), and use `from voc import collector as co` followed by
`snapshot = co.get_dataset()`. Files saved under `notebooks/` appear on the host.
The dataset dashboard is at <http://127.0.0.1:8050>.

To prove personal artifact writes, run these inside the container:

```bash
python -m src.mlops.check_zenml
python -m src.mlops.check_mlflow
```

Both checks must pass with artifact URIs under
`members/<your-member-id>/<your-installation-id>/`. Their run IDs differ because
they are separate diagnostics. Your ZenML (8237) and MLflow (5000) dashboards
show your own runs. These diagnostics do not advance shared dataset latest.
After host `just down` / `just up`, use `--reload` on both commands to check that
metadata and artifact access survived recreation.

### Startup choices

- `VOC_AWS_AUTH_MODE=env-file` in `.env`: Compose reads the ignored
  `config/aws.local.env`; override its path with `VOC_AWS_ENV_FILE` if needed.
  The launcher clears inherited AWS/member/storage variables before Compose so
  this selected file supplies those values, including an optional session token.
  `.env` still controls ports, mounts and the notebook login token.
- `VOC_AWS_AUTH_MODE=1password`: keep the existing `config/aws.refs.env` and
  `op run` workflow. This remains the default when no mode is specified, so
  existing installations keep working.
- `just up-local`: explicitly use local artifacts and local dataset reads without
  credentials. A fresh reader has no local publication until one is created.

The launcher never sources a credential file as shell code. Compose validates
required AWS fields before starting. To rotate credentials, update your selected
source and recreate with `just up`; running containers do not refresh keys.

`VOC_SEED_DIR` is optional. Leave it empty for readers. Publishers with an existing
seed path keep that setting; the Justfile then adds `compose.seed.yaml`, mounting
`/seed` read-only and setting `VOC_SEED_PATH`. Direct Compose users must explicitly
include that override when they need the seed.

### Isolated onboarding rehearsal

Use a separate checkout of the committed branch, not a copy of your current
runtime. In that checkout, copy `config/onboarding.env.example` to `.env` instead
of `.env.example`, set its login token, and create a new `config/aws.local.env`
with member ID `ness-onboarding` (or another rehearsal identifier).

This selects project `voc-onboarding`, an initially empty `./data`, no seed, and
ports **8238** (ZenML), **5001** (MLflow), **8889** (Jupyter), **2720** (Marimo),
and **8051** (dataset dashboard). Open a new terminal without exported VOC/Compose
settings, which otherwise take precedence over `.env`. Do not copy `data/`,
`.env`, or credentials from the publisher checkout. Use `just down` from the
rehearsal checkout to stop only that project.

This simulates an independent installation using your supplied AWS credentials;
it does not prove a different IAM identity's permissions. Current bucket-wide
permissions mean prefixes separate application output, not IAM access rights.


## Checking S3 access from Docker

This checkpoint supplies AWS credentials to the container and tests a small
object. Pipelines still use their existing artifact stores.

On your host, install AWS CLI and 1Password CLI, enable desktop CLI
integration, and complete the host upload/readback check. From the repository
root, create your personal reference file:

```bash
cp -n config/aws.refs.env.example config/aws.refs.env
```

`config/aws.refs.env` is excluded from Git and Docker builds; the `.example`
file is tracked. The existing `/workspace` bind mount still makes the reference
file visible inside the running container. It contains references, not keys.
On Windows, these shell commands and the current Justfile require a POSIX shell
(such as Git Bash); native PowerShell execution has not been validated. Each
member keeps their own values in this file.

Edit that file. In 1Password, use **Copy Secret Reference** for the access key ID
and secret access key fields. Paste the `op://...` references, not the secret
values. Fill in your bucket, region code, member ID, and the key of the host
marker containing `VOC S3 access check` followed by a newline. If you use a
section in your item, the copied reference includes that section.

Keep your existing repository `.env` settings (storage mode, seed and Jupyter).
The host reference file supplies only the AWS checkpoint values. You can select
another reference file with `VOC_AWS_REFS_FILE`.

From the host, run:

```bash
just up
just s3-check
```

`up` delegates to `up-aws`; in 1Password mode it resolves references through
`op run`, validates that credentials are present, and applies `compose.aws-credentials.yaml` when creating the container.
It builds with the updated lock and reuses your current bind directory or named
volume. `s3-check` runs `src/mlops/check_s3.py` inside that container: it checks
identity, reads the host marker, and writes/reads a unique container marker.
Expect JSON with `"status": "passed"`; compare its `caller_arn` with your host
identity. Diagnostic objects remain under `members/<id>/tests/`.

`just shell`, `just down`, `just status`, and `just logs` still work without
1Password authentication. Use `just up` on subsequent starts to provide AWS
access. `just up-aws` remains a compatible alias. Use `just up-local` to start
without credentials and explicitly select local artifact storage and local dataset reads.

Credentials are injected at runtime, never into image build arguments. Docker
retains them in container metadata: do not share expanded Compose/environment
or Docker inspection output. Expiring credentials require a fresh credential
session and container recreation with `just up`; injection does not refresh
them automatically. Direct field references do not perform a plugin's role
assumption or MFA flow, so compare host and container identities.


## Checking ZenML artifact storage

After `just s3-check` passes, set `VOC_ARTIFACT_DESTINATION=s3` in your ignored
`config/aws.refs.env`, then run `just up` and `just shell`. Inside the container:

```bash
python -m src.mlops.check_zenml
python -m src.mlops.check_zenml --reload
```

Expect `status: passed`, `row_count: 2`, and an S3 artifact URI under your personal
installation's `zenml-artifacts` prefix. The second command reloads the same
artifact ID recorded in `/data/checks/zenml-s3.json`, without running the pipeline
again. After `just down` / `just up`, repeat `--reload` to prove persistence.
This diagnostic does not collect or publish datasets.

ZenML now selects a separate S3 stack at startup while preserving the local stack.
Set the destination back to `local` and recreate the container to switch back.
MLflow also selects a new S3 experiment while preserving `voc-datasets` locally.
Both tools keep their metadata databases in `/data`; shared dataset reads can be selected independently.

## Checking MLflow artifact storage

With `VOC_ARTIFACT_DESTINATION=s3` and the rebuilt container started via
`just up`, run inside the container:

```bash
python -m src.mlops.check_mlflow
python -m src.mlops.check_mlflow --reload
```

Expect `status: passed`, the experiment `voc-datasets-s3-<installation-id>`, and
an artifact URI under your personal `mlflow-artifacts` prefix. The check writes
one tiny text file and downloads it through MLflow to compare its contents.
The reload uses the same run ID saved in `/data/checks/mlflow-s3.json`.
After `just down` / `just up`, repeat `--reload` inside the new container.

Open http://127.0.0.1:5000/ and select that experiment, then its
`artifact-storage-check` run. Its Artifacts tab contains `storage-check.txt`.
Existing local runs stay in `voc-datasets`. The dataset pipeline selects the
same configured experiment, but logs metrics and references rather than copying
its ZenML dataset into MLflow. This diagnostic does not publish shared datasets.

`src/mlops/tracking.py` centralizes experiment selection and server arguments.
The server uses a local SQLite backend with `--no-serve-artifacts` and the
configured `--default-artifact-root`. Python clients access S3 directly using
the container credentials. Do not set `MLFLOW_TRACKING_URI` to an S3 URI.
Switching destinations changes new writes, without moving previous runs.


## Checking shared dataset publication

Shared exports are an explicit action, separate from personal ZenML/MLflow storage.
Keep personal or shared reads selected independently with `dataset_source`.
Rebuild/start using `just up`, then enter with `just shell` and run:

```bash
python -m src.mlops.check_shared
python -m src.mlops.check_shared --reload
```

This creates two synthetic two-row releases under your personal
`members/<member>/<installation>/tests/shared/<check-id>/` prefix. It verifies
raw reuse, exact bytes, idempotent retry and rejection of an older release trying
to replace latest. It does not write to `voc/datasets/`. The reload creates no
objects and reads `/data/checks/shared-publication.json` to locate the same pair.
Diagnostic files remain in S3; there is no automatic deletion.

After that check passes, inspect `voc dataset-info` and choose its `run_id`:

```bash
voc dataset-info
voc-ml dataset share COMPLETED_PUBLISHED_RUN_UUID
```

Replace the placeholder with the selected run ID. Sharing validates that completed
run against its personal publication, loads the exact raw revision referenced by
its workable artifact, exports both to Parquet and verifies their round trips.
Files and a manifest are uploaded to the configured shared dataset root; shared
`latest.json` advances only after all immutable objects have been verified.
It does not collect new reviews or run the pipeline again. `voc-ml dataset publish`
still refers only to the personal catalog.

Repeat the same share command after an interrupted upload. Its saved release ID,
export bytes and original latest precondition live under
`/data/shared-publications/`. Preserve that directory for retries. If someone else
has advanced latest, the old attempt is rejected; inspect the newer release before
choosing a new successful pipeline run to share. There is no force-overwrite flag.

For this initial implementation, each export is limited to 1 GiB and uses one
conditional S3 upload. New and reused objects are downloaded for SHA-256 validation;
this favors a verifiable first implementation over minimal transfer. Shared raw
snapshots are reused by revision ID; different raw revisions are full snapshots.
These exports do not back up raw revision history or experiment metadata.


## Working with notebooks

JupyterLab opens the repository's `notebooks/` directory (`/workspace/notebooks`
in the container). The repository bind mount makes notebooks saved there visible
on the host and available to commit. The example is under `templates/`.
Older notebooks in `/data/notebooks` remain there; they are not moved or deleted.
After changing the Jupyter root, recreate the container with `just up` and
reopen JupyterLab's root URL.


### Marimo as an alternative

`just up` (or `just up-local`) also starts the Marimo editor at
<http://127.0.0.1:2718>. Enter the same `JUPYTER_TOKEN` configured in your ignored
`.env` if prompted. No new dependency installation or separate startup command is
needed. You can override the host port with `VOC_MARIMO_PORT` in `.env`.

Both editors open `/workspace/notebooks`, backed by the host repo's `notebooks/`.
Jupyter saves `.ipynb` files; Marimo saves `.py` notebooks. They share the installed
VOC package, storage configuration and credentials. Marimo runs in the project
Python environment (`--no-sandbox`). Its generated `__marimo__/` session/cache
folders are ignored by Git and Docker.

1. Recreate the container with `just up`, then open the Marimo URL.
2. Open `templates/shared-dataset.py`. The introduction should appear immediately.
3. Click **Load dataset**. Confirm the snapshot ID and row count match
   `voc dataset-info` for the configured source.
4. Create a notebook from the editor's home page and save it under `notebooks/`.
   Add `print("hello world")` to a cell and run it. Confirm its `.py` file appears
   on the host and is still available after `just down` / `just up`.

Marimo reruns dependent cells when their inputs change. Keep collection and
publication operations behind explicit user actions; the starter is read-only.
The existing update-and-compare Jupyter notebook is unchanged. Marimo does not
silently convert existing `.ipynb` files into `.py` notebooks.

`services.py` supervises the editor alongside the other tools; Docker checks its
`/health` endpoint. Editor logs are in `/data/logs/marimo.log` (`just logs`). The
dataset dashboard remains at port 8050 and Jupyter at port 8888.

See [Marimo's project environment guide](https://docs.marimo.io/guides/package_management/projects/)
for how directory editing and the shared Python environment work.


## Reading shared datasets automatically

First verify the reader using an empty temporary runtime (inside the container):

```bash
python -m src.mlops.check_shared_reader
```

This reads shared latest, downloads matching raw/workable exports, validates their
hashes/schemas, and reloads them with network access disabled. It requires no seed,
raw database, personal catalog or ZenML/MLflow metadata. It uploads nothing. The
temporary cache is removed afterward; `/data/checks/shared-reader.json` records
its result. Expect two initial Parquet downloads and zero on cached rereads.

Use the library in a notebook:

```python
from voc import collector as co

snapshot = co.get_dataset(source="s3")
raw = co.get_dataset(source="s3", stage="raw", version=snapshot.info["release_id"])
pinned = co.get_dataset(source="s3", version=snapshot.info["release_id"])
assert pinned.data.equals(snapshot.data)
```

To make shared reads the default, add `VOC_DATASET_SOURCE=s3` to your ignored
`config/aws.refs.env` and recreate with `just up`. The checked-in default in
`config/storage.toml` stays `local`. Compose forwards the optional environment
selection and sets an absolute `VOC_STORAGE_CONFIG` path, so notebooks read the
same settings even when their working directory is under `notebooks/`.

`co.get_dataset()` and the dataset dashboard then use the shared release. CLI
commands support the same selection:

```bash
voc dataset-info --source s3
voc summary --source s3
voc dataset-info --source local
```

Personal lookup remains `co.get_dataset(source="local")`; its artifact bytes may
still live in personal S3 storage. Direct collector updates and `get_raw_dataset`
always use the mutable local raw store. The sharing runner and ML verification
explicitly use the personal catalog even when shared reads are the default.

The cache is under `/data/cache/datasets/`, partitioned by bucket/prefix and file
identity/checksum. Latest is checked online for each fresh request; an offline
latest request reports an error. A previously cached pinned release can be read
offline. Checksums and schema are validated on reuse; corrupt entries are fetched
again or fail visibly. Metadata-only CLI queries do not download the dataframe.


## Updating and comparing releases in a notebook

Open `notebooks/samples/update-and-compare-releases.ipynb` in JupyterLab. This publisher
walkthrough pins the current shared release, runs the collector update, prepares
and publishes a new workable snapshot, explicitly shares it, and compares both
releases. Execute cells in order: update and share are live write operations.

The notebook saves its baseline release, operation ID, raw revision, pipeline run
and new release under `/data/notebook-checkpoints/update-comparison.json`.
Keep `START_NEW_COMPARISON = False` to resume, including after restarting the
kernel/container. Set it to `True` only when starting another update session;
the notebook archives the previous checkpoint before creating a new one.

Charts show platform totals and added/changed/removed identities separately.
Edits include rating or other field changes, not only text. Net growth is added
minus removed; downloaded duplicate observations do not increase the row count.
Apple's recent-feed limit means the update cannot guarantee complete historical
coverage. Read the collection report before continuing to preparation/sharing.
