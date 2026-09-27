# VOC framework

VOC provides collection, recoverable raw revisions, published dataset queries and summaries. It is a root-level Python package with no dependency on the ML project's `src/`. ML preparation and orchestration consume this package.

## Collector

```python
from voc import collector as co

config = co.load_config("config/collector.toml")  # validated dictionary
# Once only; never overwrites an initialized store:
co.initialize_raw_store(config["raw_store"], seed_path="/seed/dataset.db")

result = co.update(store=config["raw_store"], config=config)
result.require_success()
print(result.revision_id, result.report["counts"])
raw = co.get_raw_dataset(config["raw_store"], revision=result.revision_id)
assert raw.info["revision_id"] == result.revision_id
assert raw.data.record_id.is_unique
```

`update` makes live requests unless a test transport is injected. It returns a small `UpdateResult`, not a full raw dataframe. Calling it does not launch a pipeline, prepare data or publish a snapshot. `require_success()` raises on incomplete collection. Use `get_raw_dataset` explicitly for inspection.

An operation ID identifies a logical update. Successful retries return its original revision without refetching. Configuration must match; an automatically generated cutoff is restored from the original operation. Failed attempts retain separate immutable captures beneath the operation directory. A retry can collect again. Concurrent updates check their parent revision during the atomic commit; a stale parent is rejected rather than losing changes.

Native review identities are scoped by provider, app and review ID. Seed Apple/RSS identities already linked to Appbot IDs retain those canonical record IDs. No new Apple/Appbot matching is attempted. Unchanged observations do not add row versions; changed content adds a version; older observations cannot roll back a newer source version. Checkpoints and raw changes commit together only after both source reports succeed.

## Published datasets

```python
from voc import collector as co
from voc.datasets import summary

snapshot = co.get_dataset(stage="prepared", version="latest")
info, df = snapshot.info, snapshot.data
assert len(df) == info["row_count"]
assert df.record_id.is_unique
assert df.model_text.str.strip().ne("").all()

pinned = co.get_dataset(version=info["artifact_id"])
assert pinned.data.equals(df)
counts = summary(snapshot)
assert sum(counts["platform_counts"].values()) == len(df)
```

`latest` resolves the last successful publication once, then loads that exact ZenML artifact. A failed/candidate artifact is never implicitly selected. Metadata includes the producing run, committed raw revision, schema version, preparation rules, row/content counts, checksum, project source identity and MLflow run reference. MLflow is not consulted to find or load a dataset.

The default `ZenMLReader` is an optional adapter requiring ZenML in the environment. The core package can install independently; alternate readers implement `ArtifactReader.read(artifact_id) -> DataFrame` and can be passed to `voc.datasets.get_dataset`. Later S3 access belongs to a configured ZenML artifact store, not notebook filesystem paths. Moving historical artifacts and the raw SQLite store remain separate migration tasks.

## CLI and rendering

`voc version`, `voc dataset-info --version latest` and `voc summary` wrap package queries. They do not invoke training or dataset pipelines. The eventual CLI can expose all framework capabilities, including collection and future agent-facing queries. `voc-ml` is a separate distribution whose entry point lives under `src/mlops/`.

`voc.dashboard` provides a minimal HTTP renderer using the same `get_dataset` and `summary` functions. It displays the published identity and platform/month counts. There are no embeddings, sentiment results or a result warehouse in this iteration.

## Install the framework alone

From an environment outside the prototype: `pip install /path/to/005-mlops-setup`.
This installs `voc` and its core dependencies, without the `src` package, `voc-ml`, ZenML, or MLflow. The full locked development environment is documented in the README.

## Python reference

::: voc.collector

::: voc.datasets

::: voc.store.Snapshot
