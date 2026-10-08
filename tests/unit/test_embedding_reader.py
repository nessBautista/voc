"""Independent consumer, exact alignment, cache integrity and fresh latest lookup."""
# ruff: noqa: F811 -- pytest injects imported shared fixtures

import io
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from tests.integration.test_shared_reader import CountingS3
from tests.unit.test_embedding_release import (  # noqa: F401
    completed,
    manifest_of,
    outputs,
    release,
    runtime,
)
from tests.unit.test_storage_bootstrap import storage
from voc import get_embeddings
from voc.cli import main
from voc.datasets.manifest import frame_hash
from voc.embeddings.contracts import (
    DatasetIdentity,
    InputSelection,
    canonical_json,
    feature_identity,
    fingerprint,
)
from voc.embeddings.namespace import scope_prefix
from voc.embeddings.reader import SharedEmbeddingReader
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


def test_latest_freshness_and_single_resolution(published):
    client, settings, snapshot, release, prefix = published
    reader = SharedEmbeddingReader(settings, client=client)
    reader.read(dataset=snapshot)
    client.reads.clear()
    reader.read(dataset=snapshot)
    assert client.reads == [prefix + "/latest.json"]
    pointer = json.loads(client.objects[prefix + "/latest.json"])
    pointer.update(release_id=str(uuid4()))
    pointer["manifest_key"] = prefix + "/releases/" + pointer["release_id"] + ".json"
    client.objects[prefix + "/latest.json"] = canonical_json(pointer)
    with pytest.raises(LookupError, match="does not exist"):
        reader.read(dataset=snapshot)
    assert (
        read(published).info["embedding_release_id"] == release["embedding_release_id"]
    )


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


def make_subset(published):
    client, _, snapshot, release, prefix = published
    # This unit fixture declares a genuinely larger source, keeping three selected rows.
    snapshot.data = pd.concat(
        [snapshot.data, pd.DataFrame({"record_id": ["d"], "model_text": ["extra"]})],
        ignore_index=True,
    )
    snapshot.info.update(row_count=4, content_sha256=frame_hash(snapshot.data))
    key = f"{prefix}/releases/{release['embedding_release_id']}.json"
    manifest = json.loads(client.objects[key])
    manifest["dataset"] = snapshot.info
    selection = InputSelection(
        DatasetIdentity(**snapshot.info),
        3,
        manifest["selection"]["rows_sha256"],
        limit=3,
    )
    manifest["selection"] = selection.as_dict()
    feature = manifest["feature"]
    feature["feature_id"] = feature_identity(
        selection.selection_id, feature["policy"], feature["rows_sha256"]
    )
    manifest["coverage"]["source_rows"] = 4
    client.objects[key] = canonical_json(manifest)
    pointer = json.loads(client.objects[prefix + "/latest.json"])
    pointer["manifest_sha256"] = fingerprint(manifest)
    client.objects[prefix + "/latest.json"] = canonical_json(pointer)


def test_subset_requires_opt_in_and_latest_never_selects_subset(published):
    make_subset(published)
    client, settings, snapshot, _, prefix = published
    with pytest.raises(ValueError, match="allow_subset=True"):
        read(published)
    assert not any(key.endswith(".npy") for key in client.reads)
    bundle = read(published, allow_subset=True)
    assert len(bundle.reviews) == 3 and bundle.info["coverage"]["source_rows"] == 4
    with pytest.raises(ValueError, match="Subset"):
        SharedEmbeddingReader(settings, client=client).read(
            dataset=snapshot, allow_subset=True
        )
    del client.objects[prefix + "/latest.json"]
    with pytest.raises(LookupError, match="No full embedding release"):
        SharedEmbeddingReader(settings, client=client).read(dataset=snapshot)


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


def test_corrupt_cache_fails_offline_instead_of_returning_bad_vectors(published):
    client, settings, _, _, _ = published
    read(published)
    path = cached_folder(settings) / "vectors.npy"
    path.write_bytes(b"corrupt")
    client.offline = True
    with pytest.raises(OSError):
        read(published)
    assert path.read_bytes() == b"corrupt"
    assert not list(path.parent.parent.glob(".download-*"))


@pytest.mark.parametrize(
    "damage",
    [
        "missing-manifest",
        "malformed",
        "oversize",
        "schema",
        "dataset",
        "profile",
        "missing-vectors",
        "size",
        "checksum",
        "mapping",
    ],
)
def test_invalid_remote_release_never_commits_cache(published, damage, tmp_path):
    client, settings, _, release, prefix = published
    key = f"{prefix}/releases/{release['embedding_release_id']}.json"
    vector_key = f"{prefix}/releases/{release['embedding_release_id']}/vectors.npy"
    manifest = json.loads(client.objects[key])
    if damage == "missing-manifest":
        del client.objects[key]
    elif damage == "malformed":
        client.objects[key] = b"{"
    elif damage == "oversize":
        client.objects[key] = b" " * (1024**2 + 1)
    elif damage in ("schema", "dataset", "profile"):
        if damage == "schema":
            manifest["schema_version"] = "unsupported"
        elif damage == "dataset":
            manifest["dataset"]["file_sha256"] = "b" * 64
        else:
            manifest["profile"]["profile_id"] = "b" * 64
        client.objects[key] = canonical_json(manifest)
    elif damage == "missing-vectors":
        del client.objects[vector_key]
    elif damage == "size":
        client.objects[vector_key] += b"larger"
    elif damage == "checksum":
        client.objects[vector_key] = b"x" * len(client.objects[vector_key])
    else:
        frame = pd.read_parquet(Path(release["directory"]) / "reviews.parquet")
        frame.loc[1, "vector_row"] = 0
        stream = io.BytesIO()
        frame.to_parquet(stream, index=False)
        payload = stream.getvalue()
        item = manifest["artifacts"]["reviews"]
        import hashlib

        item.update(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
        client.objects[prefix + "/" + item["key"]] = payload
        client.objects[key] = canonical_json(manifest)
    with pytest.raises((ValueError, LookupError)):
        read(published)
    assert not list(settings.runtime_root.rglob("verified.json"))
    assert not list(settings.runtime_root.rglob(".download-*"))


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


def test_public_api_cli_and_no_inference_imports(published, monkeypatch, tmp_path):
    client, settings, snapshot, release, _ = published
    monkeypatch.setattr("voc.embeddings.reader.load_storage", lambda **kwargs: settings)
    monkeypatch.setattr(
        "voc.embeddings.reader.boto3.client", lambda *args, **kwargs: client
    )
    assert (
        get_embeddings(dataset=snapshot, version=release["embedding_release_id"]).info[
            "embedding_release_id"
        ]
        == release["embedding_release_id"]
    )
    monkeypatch.setattr("voc.collector.get_dataset", lambda **kwargs: snapshot)
    args = [
        "embeddings",
        "inspect",
        "--dataset-version",
        snapshot.info["release_id"],
        "--version",
        release["embedding_release_id"],
        "--json",
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["artifact_content_verified"] is True
    invalid = CliRunner().invoke(main, args + ["--profile", "unknown"])
    assert invalid.exit_code != 0 and json.loads(invalid.stdout)["state"] == "error"
    # A separate interpreter has no producer state and forbids all ML imports.
    frame_path = tmp_path / "snapshot.parquet"
    snapshot.data.to_parquet(frame_path, index=False)
    script = """
import importlib.abc, json, sys
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'voc_ml','zenml','mlflow','torch','transformers','sentence_transformers'}:
            raise AssertionError('Forbidden import: ' + fullname)
sys.meta_path.insert(0, Block())
import pandas as pd
from voc.models import Snapshot, StorageSettings
from voc.embeddings.reader import SharedEmbeddingReader
class Offline:
    def get_object(self, **kwargs):
        raise AssertionError('Network should not be used')
settings = StorageSettings('local','s3',Path(sys.argv[1]),'test-bucket','us-east-2','ness','voc/datasets','members',None)
snapshot = Snapshot(pd.read_parquet(sys.argv[2]), json.loads(sys.argv[3]))
bundle = SharedEmbeddingReader(settings, client=Offline()).read(dataset=snapshot, version=sys.argv[4])
assert bundle.vectors.shape == (3,384)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(settings.runtime_root),
            str(frame_path),
            json.dumps(snapshot.info),
            release["embedding_release_id"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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
