"""Immutable uploads and conditional promotion, including crash/retry boundaries."""

# ruff: noqa: F811 -- pytest injects the imported shared fixtures

import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from click.testing import CliRunner

from tests.integration.test_shared_publication import MemoryS3
from tests.unit.test_embedding_release import (  # noqa: F401
    completed,
    manifest_of,
    outputs,
    release,
    rewrite_manifest,
    runtime,
)
from tests.unit.test_storage_bootstrap import storage
from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import DatasetIdentity, InputSelection, feature_identity
from voc.embeddings.publisher import publish_release
from voc.storage.objects import PublicationConflict
from voc.storage.settings import load_storage
from voc_ml.cli import main


@pytest.fixture
def destination(tmp_path):
    return replace(storage(tmp_path, "local"), bucket="test-bucket")


def saved(settings, reference):
    with sqlite3.connect(
        settings.runtime_root / "shared-publications/embeddings/attempts.db"
    ) as db:
        return json.loads(
            db.execute(
                "SELECT record FROM attempts WHERE producer_id=?",
                (reference["producer_run_id"],),
            ).fetchone()[0]
        )


def clone(reference, tmp_path):
    reference = dict(reference)
    manifest = manifest_of(reference)
    folder = tmp_path / str(uuid4())
    shutil.copytree(reference["directory"], folder)
    reference.update(
        directory=str(folder),
        embedding_release_id=str(uuid4()),
        producer_run_id=str(uuid4()),
    )
    manifest["embedding_release_id"] = reference["embedding_release_id"]
    manifest["provenance"]["producer_run_id"] = reference["producer_run_id"]
    for name, filename in (("vectors", "vectors.npy"), ("reviews", "reviews.parquet")):
        manifest["artifacts"][name]["key"] = (
            f"releases/{reference['embedding_release_id']}/{filename}"
        )
    rewrite_manifest(reference, manifest)
    reference["manifest_sha256"] = file_hash(folder / "manifest.json")
    return reference


def test_upload_order_retry_and_separate_promotion(release, destination):
    client = MemoryS3()
    first = publish_release(release, destination, client=client)
    assert first["state"] == "published" and first["promotion"] is None
    assert len(client.writes) == 3
    assert client.writes[0].endswith("vectors.npy")
    assert client.writes[1].endswith("reviews.parquet")
    assert client.writes[2].endswith(release["embedding_release_id"] + ".json")
    assert publish_release(release, destination, client=client) == first
    assert len(client.writes) == 3
    second = publish_release(release, destination, client=client, promote=True)
    assert second["state"] == "promoted" and client.writes[-1].endswith("latest.json")
    assert second["publication_id"] == first["publication_id"]
    assert publish_release(release, destination, client=client, promote=True) == second
    assert len(client.writes) == 4
    assert saved(destination, release)["promotion"]["state"] == "promoted"


@pytest.mark.parametrize("suffix", ["vectors.npy", "reviews.parquet", ".json"])
def test_interrupted_upload_persists_intent_and_retries(release, destination, suffix):
    client = MemoryS3()
    client.fail_suffix = suffix
    with pytest.raises(OSError):
        publish_release(release, destination, client=client)
    record = saved(destination, release)
    assert record["upload_state"] == "failed"
    assert not any(key.endswith(".json") for key in client.objects)
    client.fail_suffix = None
    receipt = publish_release(release, destination, client=client)
    assert receipt["publication_id"] == record["publication_id"]
    assert len(client.writes) == 3


@pytest.mark.parametrize(
    "suffix", ["vectors.npy", "reviews.parquet", ".json", "latest.json"]
)
def test_lost_response_recovers_without_new_identity(release, destination, suffix):
    client = MemoryS3()
    client.lose_suffix = suffix
    with pytest.raises(OSError):
        publish_release(release, destination, client=client, promote=True)
    before = saved(destination, release)
    receipt = publish_release(release, destination, client=client, promote=True)
    assert receipt["publication_id"] == before["publication_id"]
    assert receipt["promotion"]["attempt_id"] == before["promotion"]["attempt_id"]
    assert receipt["promotion"]["expected_etag"] == before["promotion"]["expected_etag"]
    assert len(client.writes) == 4


def test_corrupt_remote_object_cannot_publish_manifest(release, destination):
    client = MemoryS3()
    client.fail_suffix = "reviews.parquet"
    with pytest.raises(OSError):
        publish_release(release, destination, client=client, promote=True)
    key = client.writes[0]
    client.objects[key] = b"corrupt"
    client.fail_suffix = None
    with pytest.raises(ValueError, match="differs"):
        publish_release(release, destination, client=client, promote=True)
    assert list(client.objects) == [key]


def test_stale_promotion_keeps_original_precondition(release, destination, tmp_path):
    client = MemoryS3()
    baseline = publish_release(release, destination, client=client, promote=True)
    interrupted = clone(release, tmp_path)
    client.fail_suffix = "latest.json"
    with pytest.raises(OSError):
        publish_release(interrupted, destination, client=client, promote=True)
    attempt = saved(destination, interrupted)["promotion"]
    pointer_key = baseline["latest_uri"].removeprefix("s3://test-bucket/")
    assert (
        json.loads(client.objects[pointer_key])["release_id"]
        == release["embedding_release_id"]
    )
    client.fail_suffix = None
    newest = clone(release, tmp_path)
    publish_release(newest, destination, client=client, promote=True)
    for _ in range(2):
        with pytest.raises(PublicationConflict):
            publish_release(interrupted, destination, client=client, promote=True)
        record = saved(destination, interrupted)
        assert record["upload_state"] == "published"
        assert record["promotion"]["expected_etag"] == attempt["expected_etag"]
        assert record["promotion"]["state"] == "conflict"
    assert (
        json.loads(client.objects[pointer_key])["release_id"]
        == newest["embedding_release_id"]
    )


def test_later_promotion_captures_current_pointer(release, destination, tmp_path):
    client = MemoryS3()
    publish_release(release, destination, client=client)
    other = clone(release, tmp_path)
    publish_release(other, destination, client=client, promote=True)
    receipt = publish_release(release, destination, client=client, promote=True)
    assert receipt["promotion"]["expected_etag"] is not None
    assert saved(destination, release)["observed_etag"] is None


def test_subset_upload_allowed_but_promotion_never_writes(release, destination):
    manifest = manifest_of(release)
    manifest["dataset"]["row_count"] = 80
    selection = InputSelection(
        DatasetIdentity(**manifest["dataset"]),
        3,
        manifest["selection"]["rows_sha256"],
        limit=3,
    )
    manifest["selection"] = selection.as_dict()
    feature = manifest["feature"]
    feature["feature_id"] = feature_identity(
        selection.selection_id, feature["policy"], feature["rows_sha256"]
    )
    manifest["coverage"]["source_rows"] = 80
    rewrite_manifest(release, manifest)
    release.update(
        manifest_sha256=file_hash(Path(release["directory"]) / "manifest.json"),
        coverage=manifest["coverage"],
    )
    client = MemoryS3()
    with pytest.raises(ValueError, match="Subset"):
        publish_release(release, destination, client=client, promote=True)
    assert not client.writes
    assert publish_release(release, destination, client=client)["state"] == "published"
    assert not any(key.endswith("latest.json") for key in client.objects)


@pytest.mark.parametrize(
    "field,value", [("embeddings_prefix", "other-embeddings"), ("region", "us-east-1")]
)
def test_retry_rejects_changed_destination(release, destination, field, value):
    client = MemoryS3()
    publish_release(release, destination, client=client)
    with pytest.raises(ValueError, match="Saved publication intent differs"):
        publish_release(release, replace(destination, **{field: value}), client=client)
    assert len(client.writes) == 3


def test_corrupt_export_or_wrong_source_fails_before_s3(release, destination):
    client = Mock()
    with pytest.raises(ValueError, match="configured shared dataset"):
        publish_release(
            release, replace(destination, shared_prefix="other-datasets"), client=client
        )
    (Path(release["directory"]) / "vectors.npy").write_bytes(b"bad")
    with pytest.raises(ValueError):
        publish_release(release, destination, client=client)
    assert not client.mock_calls


def test_cli_adapter_never_encodes_and_returns_clean_json(
    completed, destination, monkeypatch
):
    from voc_ml.embeddings import publish, sentence_encoder

    client = MemoryS3()
    monkeypatch.setattr(publish, "load_storage", lambda: destination)
    monkeypatch.setattr(
        "voc.embeddings.publisher.boto3.client", lambda *args, **kw: client
    )
    encoder = Mock(side_effect=AssertionError("must not encode"))
    monkeypatch.setattr(sentence_encoder, "SentenceEncoder", encoder)
    for _ in range(2):
        result = CliRunner().invoke(
            main, ["embeddings", "publish", completed, "--json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["state"] == "published"
    encoder.assert_not_called()
    failed = CliRunner().invoke(main, ["embeddings", "publish", "invalid", "--json"])
    assert failed.exit_code != 0 and json.loads(failed.stdout)["state"] == "error"


@pytest.mark.parametrize(
    "prefix",
    ["voc/datasets", "voc", "voc/datasets/embeddings", "members", "members/ness"],
)
def test_storage_rejects_overlap(tmp_path, monkeypatch, prefix):
    monkeypatch.setenv("VOC_EMBEDDINGS_PREFIX", prefix)
    config = tmp_path / "storage.toml"
    config.write_text('artifact_destination = "local"\ndataset_source = "personal"\n')
    with pytest.raises(ValueError, match="overlap"):
        load_storage(config)


def test_storage_default_and_override(tmp_path, monkeypatch):
    config = tmp_path / "storage.toml"
    config.write_text('artifact_destination = "local"\ndataset_source = "personal"\n')
    monkeypatch.delenv("VOC_EMBEDDINGS_PREFIX", raising=False)
    assert load_storage(config).embeddings_prefix == "voc/embeddings"
    monkeypatch.setenv("VOC_EMBEDDINGS_PREFIX", "experiments/embeddings")
    resolved = load_storage(config)
    assert resolved.describe()["embeddings_prefix"] == "experiments/embeddings"


def test_intent_is_committed_before_first_s3_write(release, destination):
    class InspectIntent(MemoryS3):
        def put_object(self, **kwargs):
            record = saved(destination, release)
            assert record["inputs"]["export"] == release
            assert record["promotion"]["expected_etag"] is None
            return super().put_object(**kwargs)

    publish_release(release, destination, client=InspectIntent(), promote=True)


def test_concurrent_publications_share_one_intent(release, destination):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, barrier = MemoryS3(), Barrier(2)

    def publish():
        barrier.wait(timeout=10)
        return publish_release(release, destination, client=client, promote=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=30)
            for future in [pool.submit(publish), pool.submit(publish)]
        ]
    assert results[0] == results[1]
    assert len(client.writes) == 4


def test_local_change_during_upload_never_publishes_manifest(release, destination):
    class ChangeLocal(MemoryS3):
        def put_object(self, **kwargs):
            result = super().put_object(**kwargs)
            if kwargs["Key"].endswith("vectors.npy"):
                (Path(release["directory"]) / "reviews.parquet").write_bytes(b"changed")
            return result

    client = ChangeLocal()
    with pytest.raises(ValueError, match="changed after validation"):
        publish_release(release, destination, client=client, promote=True)
    assert len(client.writes) == 1


def test_upload_attempt_cannot_be_changed_to_promotion_before_finishing(
    release, destination
):
    client = MemoryS3()
    client.fail_suffix = "vectors.npy"
    with pytest.raises(OSError):
        publish_release(release, destination, client=client)
    with pytest.raises(ValueError, match="Finish the saved upload"):
        publish_release(release, destination, client=client, promote=True)
    assert not client.writes


def test_failed_promotion_requires_same_request(release, destination):
    client = MemoryS3()
    client.fail_suffix = "latest.json"
    with pytest.raises(OSError):
        publish_release(release, destination, client=client, promote=True)
    with pytest.raises(ValueError, match="--promote"):
        publish_release(release, destination, client=client)


def test_retry_rejects_changed_valid_export(release, destination, tmp_path):
    client = MemoryS3()
    publish_release(release, destination, client=client)
    changed = clone(release, tmp_path)
    manifest = manifest_of(changed)
    manifest["provenance"]["producer_run_id"] = release["producer_run_id"]
    changed["producer_run_id"] = release["producer_run_id"]
    rewrite_manifest(changed, manifest)
    changed["manifest_sha256"] = file_hash(Path(changed["directory"]) / "manifest.json")
    with pytest.raises(ValueError, match="Saved publication intent differs"):
        publish_release(changed, destination, client=client)
    assert len(client.writes) == 3
