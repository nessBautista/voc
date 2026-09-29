# Datasets domain

This package reads dataset snapshots and their metadata. It supports personal
publications and team-shared releases through the same query API.

## Dataset sources

- `source="personal"` reads your computer’s `data/publications.db` catalog,
  then asks ZenML to load the referenced artifact. That artifact may be stored
  locally or in your personal S3 space.
- `source="s3"` reads a team-shared release directly from S3, with a local cache.
  It does not require the publisher’s ZenML database or personal catalog.

`VOC_DATASET_SOURCE` selects the default. The older `source="local"` remains
an alias for `personal`. `VOC_ARTIFACT_DESTINATION` is a separate setting:
it selects where personal artifact files are written (`local` or `s3`).

For personal reads, `version="latest"` selects the latest personal publication;
a pinned version is an artifact ID. For shared reads, it selects the latest
shared release; a pinned version is a release ID.

## Modules

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Select the source, resolve metadata, load snapshots, and summarize data. |
| `catalog.py` | Store personal publication references in SQLite and resolve them. |
| `zenml_reader.py` | Load a personal publication’s dataframe artifact through ZenML. |
| `shared_reader.py` | Download, verify, and cache shared dataset releases. |
| `manifest.py` | Handle the shared release metadata contract. |
| `parquet.py` | Convert dataframes to and from the Parquet representation. |

The catalog stores references and metadata, not the dataframe itself.
Collection lives in `voc.collection`; preparation and completed-run validation
live in `voc_ml`.
