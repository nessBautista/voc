"""Inspection behavior through the public Python operation and CLI entry point."""
# ruff: noqa: F811 -- pytest injects imported shared fixtures

import io
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from click.testing import CliRunner

from tests.unit.test_embedding_reader import (  # noqa: F401
    cached_folder,
    published,
    read,
)
from tests.unit.test_embedding_release import (  # noqa: F401
    completed,
    manifest_of,
    outputs,
    release,
    runtime,
)
from voc import get_embeddings
from voc.cli import main
from voc.datasets.manifest import frame_hash
from voc.embeddings.inspection import inspect_embeddings
from voc.embeddings.reading.reader import SharedEmbeddingReader
from voc.embeddings.shared.contracts import (
    DatasetIdentity,
    InputSelection,
    canonical_json,
    feature_identity,
    fingerprint,
)
from voc.models import Snapshot


def inspect(published, **kwargs):
    client, settings, snapshot, release, _ = published
    reader = SharedEmbeddingReader(settings, client=client)
    options = {
        "dataset_version": snapshot.info["release_id"],
        "version": release["embedding_release_id"],
        **kwargs,
    }
    return inspect_embeddings(
        **options,
        dataset_reader=lambda **kwargs: snapshot,
        embedding_reader=lambda source, **kwargs: reader.read(**kwargs),
    )


@pytest.mark.parametrize(
    "options",
    [
        {"dataset_version": "invalid"},
        {"version": "invalid"},
        {"profile": "unknown"},
        {"source": "personal"},
        {"allow_subset": "yes"},
    ],
)
def test_inspection_rejects_invalid_request_before_readers(options):
    def forbidden_reader(**kwargs):
        pytest.fail("Invalid inspection must not access storage")

    with pytest.raises(ValueError):
        inspect_embeddings(
            **{"dataset_version": "latest", **options},
            dataset_reader=forbidden_reader,
            embedding_reader=forbidden_reader,
        )


def test_inspection_pins_dataset_once_when_latest_changes(published):
    client, settings, snapshot, release, _ = published
    other = Snapshot(snapshot.data, snapshot.info | {"release_id": str(uuid4())})
    snapshots = iter((snapshot, other))
    reader = SharedEmbeddingReader(settings, client=client)
    reads = []

    def moving_dataset(**kwargs):
        selected = next(snapshots)
        reads.append(selected.info["release_id"])
        return selected

    result = inspect_embeddings(
        dataset_version="latest",
        version=release["embedding_release_id"],
        dataset_reader=moving_dataset,
        embedding_reader=lambda source, **kwargs: reader.read(**kwargs),
    )
    assert result["release"]["dataset"] == snapshot.info
    assert reads == [snapshot.info["release_id"]]


def test_latest_freshness_and_single_resolution(published):
    client, _, _, release, prefix = published
    inspect(published, version="latest")
    client.reads.clear()
    inspect(published, version="latest")
    assert client.reads == [prefix + "/latest.json"]
    pointer = json.loads(client.objects[prefix + "/latest.json"])
    pointer.update(release_id=str(uuid4()))
    pointer["manifest_key"] = prefix + "/releases/" + pointer["release_id"] + ".json"
    client.objects[prefix + "/latest.json"] = canonical_json(pointer)
    with pytest.raises(LookupError, match="does not exist"):
        inspect(published, version="latest")
    assert (
        read(published).info["embedding_release_id"] == release["embedding_release_id"]
    )


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
    client, _, _, _, prefix = published
    with pytest.raises(ValueError, match="allow_subset=True"):
        inspect(published)
    assert not any(key.endswith(".npy") for key in client.reads)
    result = inspect(published, allow_subset=True)
    assert result["release"]["coverage"] == {
        "selected_rows": 3,
        "source_rows": 4,
        "unique_texts": 2,
    }
    with pytest.raises(ValueError, match="Subset"):
        inspect(published, version="latest", allow_subset=True)
    del client.objects[prefix + "/latest.json"]
    with pytest.raises(LookupError, match="No full embedding release"):
        inspect(published, version="latest")


def test_corrupt_cache_fails_offline_instead_of_returning_bad_vectors(published):
    client, settings, _, _, _ = published
    read(published)
    path = cached_folder(settings) / "vectors.npy"
    path.write_bytes(b"corrupt")
    client.offline = True
    with pytest.raises(OSError):
        inspect(published)
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
        inspect(published)
    assert not list(settings.runtime_root.rglob("verified.json"))
    assert not list(settings.runtime_root.rglob(".download-*"))


def test_public_api_cli_and_no_inference_imports(published, monkeypatch, tmp_path):
    client, settings, snapshot, release, _ = published
    monkeypatch.setattr(
        "voc.embeddings.reading.reader.load_storage", lambda **kwargs: settings
    )
    monkeypatch.setattr(
        "voc.embeddings.reading.reader.boto3.client", lambda *args, **kwargs: client
    )
    assert (
        get_embeddings(dataset=snapshot, version=release["embedding_release_id"]).info[
            "embedding_release_id"
        ]
        == release["embedding_release_id"]
    )
    monkeypatch.setattr("voc.collector.get_dataset", lambda **kwargs: snapshot)
    expected = {
        "state": "verified",
        "artifact_content_verified": True,
        "release": manifest_of(release),
    }
    assert inspect_embeddings(
        dataset_version=snapshot.info["release_id"],
        version=release["embedding_release_id"],
    ) == expected
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
    assert result.stdout == json.dumps(expected) + "\n"
    pretty = CliRunner().invoke(main, args[:-1])
    assert pretty.exit_code == 0, pretty.output
    assert pretty.stdout == json.dumps(expected, indent=2) + "\n"
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
from voc import collector
from voc.embeddings.reading import reader
from voc.embeddings.inspection import inspect_embeddings
def offline(*args, **kwargs):
    raise AssertionError('AWS client should not be created for pinned cached reads')
settings = StorageSettings('local','s3',Path(sys.argv[1]),'test-bucket','us-east-2','ness','voc/datasets','members',None)
snapshot = Snapshot(pd.read_parquet(sys.argv[2]), json.loads(sys.argv[3]))
collector.get_dataset = lambda **kwargs: snapshot
reader.load_storage = lambda **kwargs: settings
reader.boto3.client = offline
result = inspect_embeddings(dataset_version=snapshot.info['release_id'], version=sys.argv[4])
assert result['release']['embedding_release_id'] == sys.argv[4]
assert result['release']['artifacts']['vectors']['shape'] == [3,384]
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


