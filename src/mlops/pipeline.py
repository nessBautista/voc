import hashlib
import json
from typing import Annotated
import pandas as pd
from zenml import pipeline, step
from src.preparation.dataset import prepare_dataset
from voc import collector
from voc.store import RawStore


# Step 1: select a committed raw revision, optionally collecting updates first.
# Disable caching so each run executes the step instead of reusing old outputs.
# Annotated gives the returned dictionary the ZenML artifact name "raw_reference".
@step(enable_cache=False)
def raw_revision(
    config: dict,
    refresh: bool,
    operation_id: str
) -> Annotated[dict, "raw_reference"]:
    if refresh:
        # Fetch and commit through VOC; the operation ID identifies this update attempt.
        result = collector.update(
            store=config["raw_store"],
            config=config,
            operation_id=operation_id
        )
        # Stop this step on failure; update() already handles the database commit.
        result.require_success()
        revision_id, report = result.revision_id, result.report
    else:
        # Use the latest committed revision without making store requests.
        revision = RawStore(config["raw_store"]).revision()
        revision_id, report = revision["id"], json.loads(revision["report"])
    # Persist a small reference and report, not another full raw dataset.
    return {"raw_store_id": config["raw_store"],
            "raw_revision_id": revision_id, "collection_report": report}


# Step 2: produce two named artifacts: the workable DataFrame and its report.
@step(enable_cache=False)
def prepare(
    reference: dict,
    rules: dict,
    project_identity: str
) -> tuple[Annotated[pd.DataFrame, "workable"],
                       Annotated[dict, "preparation_report"]]:
    # Read the exact revision selected above, even if newer raw data now exists.
    raw = collector.get_raw_dataset(
        reference["raw_store_id"],
        reference["raw_revision_id"]
    )
    # Delegate preparation policy to the ML function tested in lesson 05.
    result = prepare_dataset(raw.data, rules)
    # Fingerprint the serialized dataset contents for later integrity checks.
    checksum = hashlib.sha256(
        result.data.to_json(
            orient="records",
            force_ascii=False
        ).encode()
    ).hexdigest()
    # Record the input revision, content fingerprint, and supplied project identity.
    result.report.update(
        raw_store_id=reference["raw_store_id"],
        raw_revision_id=reference["raw_revision_id"],
        content_sha256=checksum,
        project_identity=project_identity
    )
    # ZenML saves these outputs using the active stack's artifact store.
    return result.data, result.report


# Connect the steps: prepare depends on raw_revision's returned reference.
# With caching disabled, repeated runs create new workable snapshots.
@pipeline(enable_cache=False)
def dataset_pipeline(
    config: dict,
    rules: dict,
    refresh: bool,
    operation_id: str,
    project_identity: str
):
    reference = raw_revision(config, refresh, operation_id)
    prepare(reference, rules, project_identity)
