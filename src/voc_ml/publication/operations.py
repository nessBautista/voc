"""Publish validated dataset runs personally or share their exact raw revision."""

from __future__ import annotations

from typing import TYPE_CHECKING

from voc.collection.raw.store import RawStore
from voc.datasets import publish

from .contracts import ValidatedDatasetMetadata
from .zenml_reader import validated_run

if TYPE_CHECKING:
    from voc.datasets.publication.contracts import DatasetPublication


def publish_run(run_id) -> ValidatedDatasetMetadata:
    info, _ = validated_run(run_id)
    publish(info)
    return info


def share_run(run_id, *, storage=None) -> DatasetPublication:
    """Share an already-published local result; collection is never invoked here."""
    from voc.datasets import resolve
    from voc.datasets.publication import publish_release
    from voc.models import Snapshot
    from voc.storage import load_storage

    info, frame = validated_run(run_id)
    if resolve(info["artifact_id"], source="personal") != info:
        raise ValueError("Local publication differs from validated pipeline output")
    raw = RawStore(info["raw_store_id"]).read(info["raw_revision_id"])
    return publish_release(raw, Snapshot(frame, info), storage or load_storage())
