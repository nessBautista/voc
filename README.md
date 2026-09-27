hola


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
just up-aws
just s3-check
```

`up-aws` resolves references through `op run`, validates that credentials are
present, and applies `compose.aws-credentials.yaml` when creating the container.
It builds with the updated lock and reuses your current bind directory or named
volume. `s3-check` runs `src/mlops/check_s3.py` inside that container: it checks
identity, reads the host marker, and writes/reads a unique container marker.
Expect JSON with `"status": "passed"`; compare its `caller_arn` with your host
identity. Diagnostic objects remain under `members/<id>/tests/`.

`just shell`, `just down`, `just status`, and `just logs` still work without
1Password authentication. Use `just up-aws` on subsequent starts to provide AWS
access. Ordinary `just up` starts without this override, for local work.

Credentials are injected at runtime, never into image build arguments. Docker
retains them in container metadata: do not share expanded Compose/environment
or Docker inspection output. Expiring credentials require a fresh credential
session and container recreation with `just up-aws`; injection does not refresh
them automatically. Direct field references do not perform a plugin's role
assumption or MFA flow, so compare host and container identities.


## Checking ZenML artifact storage

After `just s3-check` passes, set `VOC_ARTIFACT_DESTINATION=s3` in your ignored
`config/aws.refs.env`, then run `just up-aws` and `just shell`. Inside the container:

```bash
python -m src.mlops.check_zenml
python -m src.mlops.check_zenml --reload
```

Expect `status: passed`, `row_count: 2`, and an S3 artifact URI under your personal
installation's `zenml-artifacts` prefix. The second command reloads the same
artifact ID recorded in `/data/checks/zenml-s3.json`, without running the pipeline
again. After `just down` / `just up-aws`, repeat `--reload` to prove persistence.
This diagnostic does not collect or publish datasets.

ZenML now selects a separate S3 stack at startup while preserving the local stack.
Set the destination back to `local` and recreate the container to switch back.
MLflow also selects a new S3 experiment while preserving `voc-datasets` locally.
Both tools keep their metadata databases in `/data`; shared dataset reads can be selected independently.

## Checking MLflow artifact storage

With `VOC_ARTIFACT_DESTINATION=s3` and the rebuilt container started via
`just up-aws`, run inside the container:

```bash
python -m src.mlops.check_mlflow
python -m src.mlops.check_mlflow --reload
```

Expect `status: passed`, the experiment `voc-datasets-s3-<installation-id>`, and
an artifact URI under your personal `mlflow-artifacts` prefix. The check writes
one tiny text file and downloads it through MLflow to compare its contents.
The reload uses the same run ID saved in `/data/checks/mlflow-s3.json`.
After `just down` / `just up-aws`, repeat `--reload` inside the new container.

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
Rebuild/start using `just up-aws`, then enter with `just shell` and run:

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
After changing the Jupyter root, recreate the container with `just up-aws` and
reopen JupyterLab's root URL.


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
`config/aws.refs.env` and recreate with `just up-aws`. The checked-in default in
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
