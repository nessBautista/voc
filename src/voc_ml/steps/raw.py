"""Select or refresh a committed local raw revision."""

import json
from typing import Annotated

from zenml import step

from voc import collector
from voc.collection.store import RawStore


@step(enable_cache=False)
def raw_revision(
    config: dict, refresh: bool, operation_id: str
) -> Annotated[dict, "raw_reference"]:
    if refresh:
        result = collector.update(
            store=config["raw_store"], config=config, operation_id=operation_id
        )
        result.require_success()
        rid = result.revision_id
        report = result.report
    else:
        raw = RawStore(config["raw_store"])
        revision = raw.revision()
        rid = revision["id"]
        report = json.loads(revision["report"])
    return {
        "raw_store_id": config["raw_store"],
        "raw_revision_id": rid,
        "collection_report": report,
    }

