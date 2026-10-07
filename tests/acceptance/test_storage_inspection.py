"""Opt-in read-only checks of an already-published release; no S3 mutations.

Set VOC_EMBEDDING_INSPECT_RUN_ID to its local producer ID. Only the saved export
reference is read locally; no export, producer, publication or tracking is invoked.
"""

import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from voc.embeddings.contracts import require_uuid
from voc.storage.inspection import inspect_storage
from voc.storage.settings import load_storage


@pytest.mark.live
def test_inspect_existing_remote_release(tmp_path):
    run_id = os.environ.get("VOC_EMBEDDING_INSPECT_RUN_ID")
    if not run_id:
        pytest.skip("Set VOC_EMBEDDING_INSPECT_RUN_ID to an already-published producer")
    require_uuid(run_id, "producer_run_id")
    settings = load_storage(destination="local")
    path = settings.runtime_root / "embeddings/runs" / run_id / "export-reference.json"
    reference = json.loads(path.read_bytes())
    overview = inspect_storage(settings)
    assert overview["state"] == "ok", overview
    detail = inspect_storage(
        settings,
        scope="embeddings",
        dataset_version=reference["dataset_release_id"],
        release=reference["embedding_release_id"],
    )
    assert detail["state"] == "ok", detail
    remote = detail["release"]
    assert remote["status"] == "metadata_consistent"
    assert remote["manifest_sha256"] == reference["manifest_sha256"]
    assert remote["coverage"] == reference["coverage"]
    manifest = json.loads(
        (
            settings.runtime_root / "embeddings/runs" / run_id / "release/manifest.json"
        ).read_bytes()
    )
    for artifact in remote["artifacts"]:
        assert (
            artifact["observed_bytes"]
            == manifest["artifacts"][artifact["name"]]["size_bytes"]
        )
    empty_settings = replace(
        settings,
        embeddings_prefix=f"{settings.embeddings_prefix}/checks/empty-{uuid4()}",
    )
    empty = inspect_storage(empty_settings, scope="embeddings")
    assert empty["state"] == "ok" and empty["scopes"][0]["listed_objects"] == 0
    (tmp_path / "storage-inspection.json").write_text(
        json.dumps({"overview": overview, "release": detail, "empty": empty}, indent=2)
    )
