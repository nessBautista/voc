# Topic modelling and sentiment demo

Workflows 01 and 02 form a standalone Marimo + Studio project. Its Python package,
Docker image, dependencies, tests and configuration live here. These workflows do not import
`voc`, `voc_ml` or `voc_dev`, start ZenML/MLflow, or read the parent application's
configuration. Workflow 03 requires the main VOC workspace for tracking.

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

### Workflow 01 sentiment model selection

Section 7 of `workflow01.py` performs inference only. Choose a checkpoint for
stores and another for X/YouTube. Each selector offers the original RoBERTuito,
Hugging Face versions `v1` and `v2` in `Banamex-voc-sentiment`, and completed local
models. Access to the private Hub repositories requires a Hugging Face login
with read permission in the environment running this notebook.

Local models from Workflows 01, 02 and 03 appear when their metrics and model
files have been saved. Press **Refresh available models** after a training run
finishes. The original model remains the default for both domains. Selecting a
model routes the existing review sample to the appropriate classifier while
preserving the topic summaries and dashboard outputs. Cache identity includes
both model choices, pinned Hub commits or local weights/tokenizers, and review
IDs/text/platforms.

Training, dataset splitting and held-out evaluation are in
`workflow03_training.py`; Workflow 01 no longer reads the manual-label CSV,
exports partitions or starts training.

### Sentiment adaptation notebook

Open `http://127.0.0.1:2730/?file=workflow02_sentiment.py` in the existing Marimo
server. This separate notebook fine-tunes the existing RoBERTuito sentiment
checkpoint on labeled reviews, selects the best epoch with
validation macro F1, and compares original/adapted models on a held-out test set.
It does not use S3 or modify `workflow01.py`.

The prepared datasets are:

- `.data/sentiment/rated_reviews.csv`: labels inferred from stars (1–2 negative,
  3 neutral, 4–5 positive), with a fixed stratified `split` column for 60% train,
  20% test and 20% validation. Equivalent preprocessed texts remain in one split;
  conflicting star labels are retained. Original text and ratings are preserved.
- `.data/sentiment/social_reviews_to_label.csv`: 100 unrated X reviews and 100
  unrated YouTube reviews, sampled with seed 42. Fill the blank `label` column
  with `negative`, `neutral` or `positive`. Export from Numbers as CSV if needed.
  Texts are unique and do not overlap the rated dataset; no relevance filter is applied.

The notebook displays both CSV paths and their class/split counts before training.
Press **Ejecutar los dos entrenamientos** to train stores first and social reviews
second. Both are independent runs starting from the original RoBERTuito checkpoint;
the second does not continue training the first model. Epochs and batch size are
shared controls. The two datasets must validate before either training starts.

The notebook respects an existing `split`; otherwise it creates a stratified
60/20/20 split after removing duplicate texts. Every CSV needs
`record_id,text,label,platform`. Supported platforms are `app_store`, `google_play`,
`x`, `youtube`. Manual data requires ten distinct texts per class as a technical
minimum. Model weights may download on first use. The store dataset can take hours
on CPU. Each experiment writes its model, metrics, splits, dataset checksum and
test predictions to `.data/sentiment/runs/<run-id>/rated/` or `social/`. The parent
`experiments.json` records each completed experiment, preserving the first result
if the second fails. Model weights are released between runs to limit memory use.
The results table compares each adapted model with its original baseline on that
dataset's held-out test set.

`python scripts/prepare-sentiment-data.py` generates these CSVs from the verified
local dataset cache and writes checksums/counts to `.data/sentiment/data_summary.json`.
It refuses to overwrite existing CSVs. Rows empty after RoBERTuito preprocessing
are excluded and their identifiers recorded in the summary. Star labels are weak
labels; they are not manually verified sentiment annotations.

Optionally supply `.data/sentiment/reviews_to_classify.csv` with `record_id,text`
and select **Tiendas** or **X y YouTube** before pressing the classification button.
This loads the selected saved model and exports `predictions.csv` in its experiment.
The notebook includes the CSV format and evaluation tables. Training is a native
PyTorch loop following the [Transformers fine-tuning pattern](https://huggingface.co/docs/transformers/v4.48.0/training).

### Topic demo files

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

## Workflow 03: independent, tracked training

Open `workflow03_training.py` in the **main workspace Marimo editor** at
<http://127.0.0.1:2721/> (not the standalone demo container). Start the main
workspace with the repository's normal `just up` command first. Its existing
ZenML stack and MLflow tracker supply the storage configuration.

1. Upload stores and social CSVs, or enter their paths inside the container.
   Required: `record_id,text,platform,label`; labels are `negative`, `neutral`,
   `positive`. Stores can instead supply a `rating` column with integer stars
   1–5. Platforms are `app_store`/`google_play` or `x`/`youtube`, respectively.
2. Set train/validation/test percentages (total 100) and a split seed. Existing
   `split` columns are recomputed. All reviews are retained, equivalent texts
   stay together, and each partition must contain all three classes. The table
   compares class percentages with the source distribution; over five percentage
   points of deviation produces a warning. Small classes incur rounding effects.
3. Set epochs, batch size, learning rate and training seed separately for each
   model. Click **Train stores model** or **Train social model** independently.
   Both start from the pinned original RoBERTuito checkpoint.

Each click creates a fresh `sentiment_training_pipeline` run in
[ZenML](http://127.0.0.1:8239), with a `train_sentiment` step linked by
`zenml_run_id` to [MLflow](http://127.0.0.1:5002). MLflow experiments are
`voc-sentiment-stores` and `voc-sentiment-social`. They record source hashes, split
percentages and seed, training parameters, epoch loss/validation macro F1, final
test metrics, per-class scores, a confusion matrix and model/tokenizer files.
Artifact files use the workspace's configured local or S3 storage.

Snapshots and local models remain under `.data/sentiment/tracked-runs/`.
The best checkpoint is selected using validation macro F1; test data is used
for final evaluation and the baseline comparison. CUDA is selected automatically
when PyTorch detects an available GPU. No training starts on opening the notebook.

## Workflow 04: transfer-learning laboratory

Open `workflow04_transfer_learning.py` in the main workspace Marimo editor at
<http://127.0.0.1:2721/>. It keeps the independent stores/social controls and
configurable stratified splits from Workflow 03, and adds:

- **Trainable layers:** head only (default), last N encoder blocks plus the head,
  or full fine-tuning. Frozen encoder modules run without dropout. The optimizer
  updates only parameters with `requires_grad=True`. Reports include actual
  trainable/total parameter counts and the names of trainable parameters.
- **Language review:** explicit buttons audit a reproducible sample of up to 500
  rows, or the entire CSV. `langdetect==1.0.9` uses a fixed seed. Texts shorter
  than the selected letter count are flagged; uncertain and other-language
  predictions remain visible for human review. Its detector scores are heuristic,
  not calibrated confidence. Short and mixed-language texts are particularly
  difficult ([detector documentation](https://github.com/Mimino666/langdetect)).
- **Optional filtering:** keep all rows by default. Spanish-only filtering requires
  a full audit and keeps only rows confidently detected as Spanish under the
  selected settings. Flagged rows can be downloaded for inspection. Filtering
  may change class proportions; the partition table is computed afterwards and
  training is blocked if a class is absent from any split.
- **Evaluation:** original/adapted macro F1, weighted F1 and accuracy on the same
  held-out test set, precision/recall/F1/support per class, loss and validation
  curves, and a labeled 3-by-3 confusion heatmap. Select the best epoch using
  validation macro F1; use test metrics for the final report.

The root ML dependency group and lockfile include `langdetect`; rebuild the
workspace normally when moving to another computer. The running workspace has
also been updated with this dependency.

Experiments appear under `voc-transfer-learning-stores` and
`voc-transfer-learning-social` in MLflow, linked to ZenML's
`transfer_learning_pipeline`. Artifact reports include `language-audit.json`,
`trainable-layers.json`, `evaluation.json` and the saved model/tokenizer. Language
reports record source hashes, sample settings, filter policy and counts; raw
review text is not included in those reports. Local output uses
`.data/sentiment/tracked-runs/`, so completed models also appear in Workflow 01's
model selectors after refreshing. Workflow 03 and its training helpers remain
unchanged.
