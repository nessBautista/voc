"""Wrap pure preparation with ZenML outputs and provenance metadata."""

import hashlib
from typing import Annotated

import pandas as pd
from zenml import step

from voc import collector
from voc_ml.preparation.dataset import prepare_dataset


@step(enable_cache=False)
def prepare(
    reference: dict, rules: dict, project_identity: str
) -> tuple[Annotated[pd.DataFrame, "workable"], Annotated[dict, "preparation_report"]]:
    raw = collector.get_raw_dataset(
        reference["raw_store_id"], reference["raw_revision_id"]
    )
    result = prepare_dataset(raw.data, rules)
    # Row content checksum is independent of Parquet writer metadata.
    checksum = hashlib.sha256(
        result.data.to_json(orient="records", force_ascii=False).encode()
    ).hexdigest()
    result.report.update(
        raw_store_id=reference["raw_store_id"],
        raw_revision_id=reference["raw_revision_id"],
        content_sha256=checksum,
        project_identity=project_identity,
    )
    return result.data, result.report

