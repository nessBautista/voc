# Review topics and sentiment demo

Open `workflow01.py` to explore the topic-modelling and sentiment pipeline and its
Studio presentation. It uses a published S3 checkpoint, with no bundled data fallback.

## Start

Use the normal VOC setup with S3 read access, `VOC_DATASET_SOURCE=s3`, and your
bucket and region configured. After pulling this version, rebuild its dependencies:

```bash
just up
just marimo
```

Open `demos/workflow01.py`. Then use Studio's **overview** view, or visit this URL
with your configured `VOC_MARIMO_PORT` (2721 by default):

```text
http://127.0.0.1:2721/studio/overview/?file=demos%2Fworkflow01.py
```

The demo pins:

- Dataset release: `b7a61a8b-27d8-46a7-b2ce-67d97268b741`.
- Demo snapshot: `188f1d136ccc01f9` under `voc/demos/prototypeV0/`.

`prototypeV0` remains the S3 identifier even though the notebook moved. Both pins
are visible in the notebook's `load_dataset` cell. The demo will not silently
follow a newer dataset or substitute local sample files.

## What loads

The first run downloads and verifies the shared dataset and eleven demo files:
the sample, embeddings, reductions, clusters, keywords, LLM answers and sentiment
results. A working copy lives in `data/demos/workflow01/<snapshot-id>/`. Verified
downloads live separately in `data/cache/`.

Subsequent runs reuse complete caches. Missing credentials, a missing snapshot,
corruption or a release mismatch stops loading. A partial working cache is not
silently completed from unrelated files. Select a new empty `SAMPLE_DIR` if you
need another working copy.

The notebook still executes calculations for its illustrations, loads model
assets where required and builds topic representations. Matching stage manifests
reuse saved expensive results. The initial model download may require internet
access. Changing parameters can trigger recalculation. OpenRouter is **optional
for viewing the published demo**. The naming button explicitly requests new LLM
answers and requires an OpenRouter key.

## Publish a new checkpoint

After finishing all stages, press **Publish demo to S3**. It uploads a complete
checkpoint and prints its ID. Publishing unchanged data is idempotent, and an
old retry cannot replace a newer shared pointer. Dataset releases and personal
ZenML/MLflow artifacts are unaffected.

To distribute new results, review and update `DEMO_VERSION` and, if applicable,
`DEMO_RELEASE_ID` in the notebook. Use a new working-cache directory for a different
pin. A single designated publisher should manage shared demo updates.

## Verify a fresh download

A fresh clone with empty `data/` reproduces a teammate's first run. A new working
copy in the same installation can still reuse its verified download cache.
To test S3 reads without deleting existing data, run this **inside the container**:

```bash
python - <<'PY'
import tempfile
from dataclasses import replace
from pathlib import Path
from voc.storage import load_storage
from voc.datasets.shared_reader import SharedDatasetReader
from voc_dev.demos import seed_prototype_cache

release = "b7a61a8b-27d8-46a7-b2ce-67d97268b741"
version = "188f1d136ccc01f9"
root = Path(tempfile.mkdtemp(prefix="voc-demo-reader-"))
settings = replace(load_storage(destination="local"), runtime_root=root)
snapshot = SharedDatasetReader(settings).read(release)
result = seed_prototype_cache(
    root / "working", release_id=release, version=version,
    workable=snapshot.data, storage=settings,
)
print(result)
print("Fresh runtime:", root)
PY
```

Expected: `files: 11` and source `S3 demo 188f1d136ccc01f9`.
The temporary directory can be removed after inspection.
