"""Reader integrity, dataset alignment, cache repair and concurrent consumption."""
# ruff: noqa: F811 -- pytest injects imported shared fixtures

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.integration.test_shared_reader import CountingS3
from tests.unit.test_embedding_release import (  # noqa: F401
    completed,
    manifest_of,
    outputs,
    release,
    runtime,
)
from tests.unit.test_storage_bootstrap import storage
from voc.embeddings.reading.reader import SharedEmbeddingReader
from voc.embeddings.shared.contracts import canonical_json
from voc.embeddings.shared.namespace import scope_prefix
from voc.models import Snapshot


@pytest.fixture
def published(release, tmp_path):
    settings = replace(storage(tmp_path / "consumer", "local"), bucket="test-bucket")
    manifest = manifest_of(release)
    frame = pd.DataFrame(
        {"record_id": ["a", "b", "c"], "model_text": ["good", "bad", "good"]}
    )
    snapshot = Snapshot(frame, dict(manifest["dataset"]))
    client = CountingS3()
    prefix = scope_prefix(
        settings, release["profile_id"], release["dataset_release_id"]
    )
    for filename in ("vectors.npy", "reviews.parquet"):
        client.objects[
            f"{prefix}/releases/{release['embedding_release_id']}/{filename}"
        ] = (Path(release["directory"]) / filename).read_bytes()
    key = f"{prefix}/releases/{release['embedding_release_id']}.json"
    client.objects[key] = (Path(release["directory"]) / "manifest.json").read_bytes()
    client.objects[prefix + "/latest.json"] = canonical_json(
        {
            "format_version": 1,
            "release_id": release["embedding_release_id"],
            "manifest_key": key,
            "manifest_sha256": release["manifest_sha256"],
        }
    )
    return client, settings, snapshot, release, prefix


def read(published, **kwargs):
    client, settings, snapshot, release, _ = published
    return SharedEmbeddingReader(settings, client=client).read(
        dataset=snapshot, version=release["embedding_release_id"], **kwargs
    )


def cached_folder(settings):
    return next(
        (settings.runtime_root / "cache/embeddings/releases").glob("*/*/manifest.json")
    ).parent


def test_independent_roundtrip_and_offline_pinned_cache(published):
    client, settings, snapshot, release, _ = published
    expected = np.load(Path(release["directory"]) / "vectors.npy")
    shutil.rmtree(Path(release["directory"]).parent)
    bundle = read(published)
    assert bundle.reviews.record_id.tolist() == ["a", "b", "c"]
    assert bundle.reviews.embedding_text.tolist() == ["good", "bad", "good"]
    assert bundle.vectors.shape == (3, 384) and not bundle.vectors.flags.writeable
    np.testing.assert_array_equal(bundle.vectors, expected)
    assert bundle.info["dataset"] == snapshot.info
    assert bundle.info["provenance"]["producer_run_id"] == release["producer_run_id"]
    assert len(client.reads) == 3
    client.offline = True
    np.testing.assert_array_equal(read(published).vectors, expected)
    assert len(client.reads) == 3
    with pytest.raises(OSError, match="network unavailable"):
        SharedEmbeddingReader(settings, client=client).read(dataset=snapshot)
    assert not (settings.runtime_root / "storage").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "text",
        "reorder",
        "id",
        "row-count",
        "extra-column",
        "raw",
        "missing-info",
        "duplicate-id",
        "empty-text",
    ],
)
def test_mutated_snapshot_rejected_before_network(published, mutation):
    client, _, snapshot, _, _ = published
    if mutation == "text":
        snapshot.data.loc[0, "model_text"] = "changed"
    elif mutation == "reorder":
        snapshot.data = snapshot.data.iloc[::-1]
    elif mutation == "id":
        snapshot.data.loc[0, "record_id"] = "changed"
    elif mutation == "row-count":
        snapshot.data = snapshot.data.iloc[:2]
    elif mutation == "extra-column":
        snapshot.data["extra"] = "changed"
    elif mutation == "raw":
        snapshot.info["stage"] = "raw"
    elif mutation == "missing-info":
        del snapshot.info["file_sha256"]
    elif mutation == "duplicate-id":
        snapshot.data.loc[1, "record_id"] = "a"
    else:
        snapshot.data.loc[0, "model_text"] = " "
    with pytest.raises(ValueError):
        read(published)
    assert client.reads == []


@pytest.mark.parametrize(
    "name", ["vectors.npy", "reviews.parquet", "manifest.json", "verified.json"]
)
def test_corrupt_cache_repaired_only_with_valid_remote_bytes(published, name):
    _, settings, _, _, _ = published
    read(published)
    path = cached_folder(settings) / name
    path.write_bytes(b"corrupt")
    assert read(published).reviews.record_id.tolist() == ["a", "b", "c"]
    assert path.read_bytes() != b"corrupt"


def test_pointer_hash_mismatch_and_cache_namespace_isolation(published):
    client, settings, snapshot, release, prefix = published
    read(published)
    pointer = json.loads(client.objects[prefix + "/latest.json"])
    pointer["manifest_sha256"] = "b" * 64
    client.objects[prefix + "/latest.json"] = canonical_json(pointer)
    with pytest.raises(ValueError, match="manifest checksum"):
        SharedEmbeddingReader(settings, client=client).read(dataset=snapshot)
    other = replace(settings, embeddings_prefix="other/embeddings")
    with pytest.raises(LookupError):
        SharedEmbeddingReader(other, client=client).read(
            dataset=snapshot, version=release["embedding_release_id"]
        )


def test_concurrent_readers_share_one_valid_cache(published):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def fetch():
        barrier.wait(timeout=10)
        return read(published).info

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fetch), pool.submit(fetch)]
        results = [future.result(timeout=30) for future in futures]
    assert results[0] == results[1]
    assert len(published[0].reads) == 3
