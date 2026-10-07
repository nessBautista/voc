"""No credentials/network: immutable demo publication, corruption and notebook seeding."""

import hashlib
import io
import json
import shutil
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from botocore.exceptions import ClientError

from voc.datasets.demos import PROTOTYPE_FILES, DemoSnapshots
from voc.datasets.manifest import json_bytes
from voc.models import StorageSettings
from voc.storage.objects import PublicationConflict
from voc_dev.demos import (
    publish_prototype_demo,
    seed_prototype_cache,
)

RELEASE = "b7a61a8b-27d8-46a7-b2ce-67d97268b741"
OTHER = "41e10f26-0864-421e-8c5d-c7bbf309d113"


@pytest.fixture
def checkpoint(tmp_path):
    """Synthetic stage artifacts: no private reviews or committed snapshot needed."""
    folder = tmp_path / "synthetic-checkpoint"
    folder.mkdir()
    texts = ["No abre la aplicación", "La navegación es sencilla", "No llega el código"]
    sample = pd.DataFrame(
        {
            "record_id": ["test-0", "test-1", "test-2"],
            "model_text": texts,
            "text_hash": [hashlib.sha256(t.encode()).hexdigest() for t in texts],
        }
    )
    sample.to_parquet(folder / "sample.parquet", index=False)
    vectors = np.eye(3, dtype=np.float32)
    reduced = np.ones((3, 5), dtype=np.float32)
    np.save(folder / "embeddings.npy", vectors, allow_pickle=False)
    np.savez(folder / "reduced.npz", cluster_5d=reduced, display_2d=reduced[:, :2])
    np.savez(
        folder / "clusters.npz",
        labels=np.array([0, 0, -1]),
        probabilities=np.array([1.0, 0.8, 0.0]),
    )
    inputs = hashlib.sha256("".join(sample["text_hash"]).encode()).hexdigest()
    manifests = {
        "embeddings.json": {"inputs_sha256": inputs},
        "reduced.json": {
            "embeddings_sha256": hashlib.sha256(vectors.tobytes()).hexdigest()
        },
        "clusters.json": {
            "reduced_sha256": hashlib.sha256(reduced.tobytes()).hexdigest()
        },
        "representations.json": {"manifest": {"inputs_sha256": inputs}, "aspects": {}},
        "labels.json": {
            "model": "fixture-model",
            "prompt_version": "fixture-v1",
            "attempts": {},
        },
        "sentiment.json": {"inputs_sha256": inputs},
    }
    for name, value in manifests.items():
        (folder / name).write_text(json.dumps(value))
    pd.DataFrame(
        {
            "record_id": sample["record_id"],
            "label": ["negative", "positive", "negative"],
        }
    ).to_parquet(folder / "sentiment.parquet", index=False)
    return folder


class MemoryS3:
    def __init__(self):
        self.objects, self.reads, self.writes = {}, [], []
        self.offline, self.fail_suffix, self.lose_response = False, None, False

    def get_object(self, *, Bucket, Key):
        if self.offline:
            raise OSError("offline")
        self.reads.append(Key)
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        data = self.objects[Key]
        return {"Body": io.BytesIO(data), "ETag": hashlib.sha256(data).hexdigest()}

    def put_object(
        self, *, Bucket, Key, Body, IfMatch=None, IfNoneMatch=None, **kwargs
    ):
        if self.offline or (self.fail_suffix and Key.endswith(self.fail_suffix)):
            raise OSError("interrupted")
        old = self.objects.get(Key)
        if (IfNoneMatch and old is not None) or (
            IfMatch and (old is None or hashlib.sha256(old).hexdigest() != IfMatch)
        ):
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[Key] = Body.read() if hasattr(Body, "read") else Body
        self.writes.append(Key)
        if self.lose_response and Key.endswith("latest.json"):
            self.lose_response = False
            raise OSError("lost successful response")
        return {}


@pytest.fixture
def setup(tmp_path):
    settings = StorageSettings(
        "local",
        "s3",
        tmp_path / "runtime",
        "team-test-bucket",
        "us-east-2",
        "tester",
        "voc/datasets",
        "members",
        None,
    )
    folder = tmp_path / "source"
    folder.mkdir()
    for name in PROTOTYPE_FILES["prototypeV0"]:
        (folder / name).write_bytes((name + " example").encode())
    client = MemoryS3()
    return DemoSnapshots("prototypeV0", settings, client=client), client, folder


def test_roundtrip_idempotent_and_offline_pinned(setup):
    demos, client, folder = setup
    version = demos.publish(folder, release_id=RELEASE)
    writes = list(client.writes)
    assert demos.publish(folder, release_id=RELEASE) == version
    assert client.writes == writes
    fetched = demos.fetch(release_id=RELEASE)
    for name in PROTOTYPE_FILES["prototypeV0"]:
        assert (fetched / name).read_bytes() == (folder / name).read_bytes()
    client.offline = True
    assert demos.fetch(version, release_id=RELEASE) == fetched
    with pytest.raises(OSError):
        demos.fetch(release_id=RELEASE)
    assert all(key.startswith("voc/demos/prototypeV0/") for key in client.writes)


def test_content_id_binds_release_and_changed_files(setup):
    demos, _client, folder = setup
    a = demos.publish(folder, release_id=RELEASE)
    b = demos.publish(folder, release_id=OTHER)
    (folder / "labels.json").write_text("changed")
    c = demos.publish(folder, release_id=OTHER)
    assert len({a, b, c}) == 3
    with pytest.raises(ValueError, match="release differs"):
        demos.fetch(release_id=RELEASE)


def test_release_mismatch_downloads_no_artifact_files(setup):
    demos, client, folder = setup
    demos.publish(folder, release_id=RELEASE)
    client.reads.clear()
    with pytest.raises(ValueError, match="release differs"):
        demos.fetch(release_id=OTHER)
    assert all(k.endswith(("latest.json", "manifest.json")) for k in client.reads)


def test_local_corruption_is_repaired_and_remote_corruption_rejected(setup):
    demos, client, folder = setup
    version = demos.publish(folder, release_id=RELEASE)
    fetched = demos.fetch(release_id=RELEASE)
    (fetched / "embeddings.npy").write_bytes(b"corrupt")
    demos.fetch(version, release_id=RELEASE)
    assert (fetched / "embeddings.npy").read_bytes() == (
        folder / "embeddings.npy"
    ).read_bytes()
    (fetched / "embeddings.npy").unlink()
    client.objects[demos.objects.key(f"snapshots/{version}/embeddings.npy")] = b"bad"
    with pytest.raises(ValueError, match="checksum or size"):
        demos.fetch(version, release_id=RELEASE)
    assert not (fetched / "embeddings.npy").exists()


def test_interrupted_upload_does_not_advance_latest_and_retries(setup):
    demos, client, folder = setup
    first = demos.publish(folder, release_id=RELEASE)
    (folder / "labels.json").write_text("new labels")
    client.fail_suffix = "sentiment.parquet"
    # Force this changed snapshot's upload to fail before its manifest/latest.
    with pytest.raises(OSError):
        demos.publish(folder, release_id=RELEASE)
    assert demos.objects.latest()[0]["snapshot_id"] == first
    client.fail_suffix = None
    second = demos.publish(folder, release_id=RELEASE)
    assert second != first


def test_lost_success_response_is_idempotent(setup):
    demos, client, folder = setup
    client.lose_response = True
    with pytest.raises(OSError):
        demos.publish(folder, release_id=RELEASE)
    version = demos.objects.latest()[0]["snapshot_id"]
    assert demos.publish(folder, release_id=RELEASE) == version


def test_old_retry_cannot_roll_back_even_from_fresh_machine(setup, tmp_path):
    demos, client, folder = setup
    old = demos.publish(folder, release_id=RELEASE)
    original = (folder / "labels.json").read_bytes()
    (folder / "labels.json").write_text("newer")
    newer = demos.publish(folder, release_id=RELEASE)
    (folder / "labels.json").write_bytes(original)
    with pytest.raises(PublicationConflict):
        demos.publish(folder, release_id=RELEASE)
    fresh = DemoSnapshots(
        "prototypeV0",
        replace(demos.storage, runtime_root=tmp_path / "other"),
        client=client,
    )
    with pytest.raises(PublicationConflict):
        fresh.publish(folder, release_id=RELEASE)
    assert demos.objects.latest()[0]["snapshot_id"] == newer != old


def test_stale_failed_attempt_cannot_replace_other_publisher(setup, tmp_path):
    demos, client, folder = setup
    client.fail_suffix = "labels.json"
    with pytest.raises(OSError):
        demos.publish(folder, release_id=RELEASE)
    client.fail_suffix = None
    other_folder = tmp_path / "other-source"
    shutil.copytree(folder, other_folder)
    (other_folder / "labels.json").write_text("newer")
    other = DemoSnapshots(
        "prototypeV0",
        replace(demos.storage, runtime_root=tmp_path / "other"),
        client=client,
    )
    winner = other.publish(other_folder, release_id=RELEASE)
    with pytest.raises(PublicationConflict):
        demos.publish(folder, release_id=RELEASE)
    assert demos.objects.latest()[0]["snapshot_id"] == winner


def test_publish_missing_file_writes_nothing(setup):
    demos, client, folder = setup
    (folder / "clusters.json").unlink()
    with pytest.raises(ValueError, match="missing regular file"):
        demos.publish(folder, release_id=RELEASE)
    assert not client.writes


@pytest.mark.parametrize("name", ["../outside", "nested/file", "sample.parquet"])
def test_manifest_rejects_traversal_or_duplicate_names(setup, name):
    demos, client, folder = setup
    version = demos.publish(folder, release_id=RELEASE)
    key = demos.objects.key(f"snapshots/{version}/manifest.json")
    value = json.loads(client.objects[key])
    value["files"][1]["name"] = name
    client.objects[key] = json_bytes(value)
    with pytest.raises(ValueError, match="expected prototype files"):
        demos.fetch(version, release_id=RELEASE)


def test_wrong_latest_manifest_hash_is_rejected(setup):
    demos, client, folder = setup
    demos.publish(folder, release_id=RELEASE)
    key = demos.objects.key("latest.json")
    value = json.loads(client.objects[key])
    value["manifest_sha256"] = "0" * 64
    client.objects[key] = json_bytes(value)
    with pytest.raises(ValueError, match="checksum differs"):
        demos.fetch(release_id=RELEASE)


def test_notebook_seeds_s3_preserves_work_and_reuses_verified_cache(
    setup, tmp_path, checkpoint
):
    demos, client, _ = setup
    workable = pd.read_parquet(checkpoint / "sample.parquet")
    version = publish_prototype_demo(
        checkpoint,
        release_id=RELEASE,
        workable=workable,
        storage=demos.storage,
        client=client,
    )
    cache = tmp_path / "working"
    result = seed_prototype_cache(
        cache,
        release_id=RELEASE,
        workable=workable,
        version=version,
        storage=demos.storage,
        client=client,
    )
    assert version in result["source"] and result["files"] == 11
    for name in PROTOTYPE_FILES["prototypeV0"]:
        assert (cache / name).read_bytes() == (checkpoint / name).read_bytes()
    labels = json.loads((cache / "labels.json").read_text())
    labels["note"] = "my work"
    (cache / "labels.json").write_text(json.dumps(labels))
    assert (
        seed_prototype_cache(
            cache,
            release_id=RELEASE,
            workable=workable,
            version=version,
            storage=demos.storage,
            client=client,
        )["files"]
        == 0
    )
    assert json.loads((cache / "labels.json").read_text())["note"] == "my work"
    client.offline = True
    # A second working copy can use the previously verified, pinned download cache.
    assert (
        seed_prototype_cache(
            tmp_path / "fresh",
            release_id=RELEASE,
            workable=workable,
            version=version,
            storage=demos.storage,
            client=client,
        )["files"]
        == 11
    )
    # A genuinely new runtime has no fallback when offline.
    fresh_storage = replace(demos.storage, runtime_root=tmp_path / "new-runtime")
    with pytest.raises(OSError):
        seed_prototype_cache(
            tmp_path / "offline",
            release_id=RELEASE,
            workable=workable,
            version=version,
            storage=fresh_storage,
            client=client,
        )


def test_notebook_does_not_mix_partial_cache_or_ignore_release(
    setup, tmp_path, checkpoint
):
    demos, client, _ = setup
    workable = pd.read_parquet(checkpoint / "sample.parquet")
    partial = tmp_path / "partial"
    partial.mkdir()
    shutil.copyfile(checkpoint / "sample.parquet", partial / "sample.parquet")
    with pytest.raises(ValueError, match="Unlabelled demo cache"):
        seed_prototype_cache(
            partial,
            release_id=RELEASE,
            workable=workable,
            storage=demos.storage,
            client=client,
        )
    (partial / "demo-source.json").write_text(
        json.dumps({"dataset_release_id": RELEASE, "snapshot_id": "a" * 16})
    )
    with pytest.raises(ValueError, match="another dataset release"):
        seed_prototype_cache(
            partial,
            release_id=OTHER,
            workable=workable,
            storage=demos.storage,
            client=client,
        )
    with pytest.raises(ValueError, match="another demo snapshot"):
        seed_prototype_cache(
            partial,
            release_id=RELEASE,
            version="b" * 16,
            workable=workable,
            storage=demos.storage,
            client=client,
        )
    with pytest.raises(ValueError, match="missing snapshot file"):
        seed_prototype_cache(
            partial,
            release_id=RELEASE,
            version="a" * 16,
            workable=workable,
            storage=demos.storage,
            client=client,
        )
    assert not (partial / "embeddings.npy").exists()


def test_notebook_rejects_mismatched_sample_and_stage(setup, tmp_path, checkpoint):
    demos, client, _ = setup
    workable = pd.read_parquet(checkpoint / "sample.parquet")
    folder = tmp_path / "snapshot"
    shutil.copytree(checkpoint, folder)
    altered = workable.copy()
    altered.loc[0, "model_text"] = "different input"
    with pytest.raises(ValueError, match="sample differs"):
        publish_prototype_demo(
            folder,
            release_id=RELEASE,
            workable=altered,
            storage=demos.storage,
            client=client,
        )
    meta = json.loads((folder / "clusters.json").read_text())
    meta["reduced_sha256"] = "0" * 64
    (folder / "clusters.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="Stale cluster"):
        publish_prototype_demo(
            folder,
            release_id=RELEASE,
            workable=workable,
            storage=demos.storage,
            client=client,
        )
    assert not client.writes


def test_cache_manifest_corruption_is_repaired(setup):
    demos, _client, folder = setup
    version = demos.publish(folder, release_id=RELEASE)
    fetched = demos.fetch(release_id=RELEASE)
    (fetched / "manifest.json").write_text("corrupt")
    assert demos.fetch(version, release_id=RELEASE) == fetched
    assert "text" in json.loads((fetched / "manifest.json").read_text())


def test_offline_cached_release_mismatch_stops_without_fallback(setup):
    demos, client, folder = setup
    version = demos.publish(folder, release_id=RELEASE)
    demos.fetch(version, release_id=RELEASE)
    client.offline = True
    with pytest.raises(ValueError, match="release differs"):
        demos.fetch(version, release_id=OTHER)


def test_conditional_promotion_rejects_a_racing_writer(setup):
    demos, client, folder = setup
    original = client.put_object
    pointer_key = demos.objects.key("latest.json")
    raced = False

    def racing_put(**kwargs):
        nonlocal raced
        if kwargs["Key"] == pointer_key and not raced:
            raced = True
            winner = {
                "schema_version": "voc-demo-snapshot-v1",
                "snapshot_id": "a" * 16,
                "manifest_key": demos.objects.key(
                    f"snapshots/{'a' * 16}/manifest.json"
                ),
                "manifest_sha256": "b" * 64,
            }
            client.objects[pointer_key] = json_bytes(winner)
        return original(**kwargs)

    client.put_object = racing_put
    with pytest.raises(PublicationConflict):
        demos.publish(folder, release_id=RELEASE)
    assert demos.objects.latest()[0]["snapshot_id"] == "a" * 16


@pytest.mark.parametrize("version", ["latest", "a" * 16])
def test_missing_demo_never_generates_replacement_data(
    setup, tmp_path, checkpoint, version
):
    demos, client, _ = setup
    workable = pd.read_parquet(checkpoint / "sample.parquet")
    with pytest.raises(LookupError):
        seed_prototype_cache(
            tmp_path / "new",
            version=version,
            release_id=RELEASE,
            workable=workable,
            storage=demos.storage,
            client=client,
        )
    assert not (tmp_path / "new/sample.parquet").exists()


def test_demo_requires_s3_mode(setup, tmp_path, checkpoint):
    demos, client, _ = setup
    with pytest.raises(ValueError, match="VOC_DATASET_SOURCE=s3"):
        seed_prototype_cache(
            tmp_path / "new",
            release_id=RELEASE,
            workable=pd.read_parquet(checkpoint / "sample.parquet"),
            storage=replace(demos.storage, dataset_source="personal"),
            client=client,
        )


@pytest.mark.parametrize(
    "prefix", ["voc/datasets", "voc/datasets/demo", "members/demo", "voc"]
)
def test_demo_prefix_must_not_overlap_other_roots(tmp_path, monkeypatch, prefix):
    from voc.storage import load_storage

    for name in (
        "VOC_STORAGE_CONFIG",
        "VOC_ARTIFACT_DESTINATION",
        "VOC_DATASET_SOURCE",
    ):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "storage.toml"
    path.write_text(f'demos_prefix = "{prefix}"\n')
    with pytest.raises(ValueError, match="must not overlap"):
        load_storage(path)
