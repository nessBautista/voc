"""Shared readers work with no publisher metadata and no ML dependency imports."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pandas as pd
import pytest
from click.testing import CliRunner
from test_shared_publication import MemoryS3, another
from test_shared_publication import pair as pair_fixture
from test_storage_bootstrap import storage

from voc import collector
from voc.datasets import get_dataset, resolve
from voc.shared import json_bytes, publish_release
from voc.shared_reader import SharedDatasetReader


@pytest.fixture
def pair(store):
    return pair_fixture.__wrapped__(store)


class CountingS3(MemoryS3):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.offline = False
        self.bad_download = False

    def get_object(self, **kwargs):
        if self.offline:
            raise OSError("network unavailable")
        self.reads.append(kwargs["Key"])
        response = super().get_object(**kwargs)
        if self.bad_download and kwargs["Key"].endswith(".parquet"):
            response["Body"] = io.BytesIO(b"broken")
        return response


@pytest.fixture
def published(tmp_path, pair):
    client = CountingS3()
    publisher = storage(tmp_path / "publisher", "s3")
    release = publish_release(*pair, publisher, client=client)
    reader_settings = replace(
        publisher,
        runtime_root=tmp_path / "reader",
        artifact_destination="local",
        installation_id=None,
    )
    client.reads.clear()
    return client, reader_settings, release, publisher


def data_reads(client):
    return [key for key in client.reads if key.endswith(".parquet")]


def test_fresh_reader_exact_pair_and_cached_latest(published, pair):
    client, settings, release, _ = published
    reader = SharedDatasetReader(settings, client=client)
    info = reader.resolve()
    assert not data_reads(client)  # CLI metadata lookup does not download rows.
    snapshot = reader.read()
    pd.testing.assert_frame_equal(snapshot.data, pair[1].data)
    raw = reader.read(snapshot.info["release_id"], stage="raw")
    pd.testing.assert_frame_equal(raw.data, pair[0].data)
    assert raw.info["raw_revision_id"] == snapshot.info["raw_revision_id"]
    assert snapshot.info == info
    assert reader.read().data.equals(snapshot.data)
    assert len(data_reads(client)) == 2
    assert client.reads.count("voc/datasets/latest.json") == 3
    assert sorted(p.name for p in settings.runtime_root.iterdir()) == ["cache"]
    assert info["release_id"] == release["release_id"]


def test_pinned_a_survives_b_and_works_offline(published, pair, monkeypatch):
    client, settings, first, publisher = published
    reader = SharedDatasetReader(settings, client=client)
    a = reader.read()
    second = publish_release(pair[0], another(pair[1]), publisher, client=client)
    assert reader.read().info["release_id"] == second["release_id"]
    client.offline = True
    assert reader.read(first["release_id"]).info == a.info
    with pytest.raises(OSError, match="network unavailable"):
        reader.read()
    # Construct a new reader with no SDK client; a pinned cache hit never creates one.
    import voc.shared_reader as module

    monkeypatch.setattr(
        module.boto3,
        "client",
        lambda *a, **k: pytest.fail("AWS client should not be created"),
    )
    assert SharedDatasetReader(settings).read(first["release_id"]).data.equals(a.data)


def test_corrupt_cache_is_repaired_and_bad_download_never_published(published):
    client, settings, release, _ = published
    reader = SharedDatasetReader(settings, client=client)
    first = reader.read()
    cache_file = next(reader.cache.glob("*.parquet"))
    cache_file.write_bytes(b"bad cache")
    client.bad_download = True
    with pytest.raises(ValueError, match="checksum"):
        reader.read(release["release_id"])
    assert cache_file.read_bytes() == b"bad cache"
    assert not list(reader.cache.glob("tmp*"))
    client.bad_download = False
    assert reader.read(release["release_id"]).data.equals(first.data)
    manifest_file = reader.cache / (release["release_id"] + ".json")
    manifest_file.write_text("invalid")
    assert reader.read(release["release_id"]).info == first.info


def test_concurrent_reads_download_once(published):
    client, settings, release, _ = published
    with ThreadPoolExecutor(max_workers=5) as workers:
        snapshots = list(
            workers.map(
                lambda _: SharedDatasetReader(settings, client=client).read(
                    release["release_id"]
                ),
                range(5),
            )
        )
    assert all(s.data.equals(snapshots[0].data) for s in snapshots)
    assert len(data_reads(client)) == 1


def test_untrusted_manifest_paths_and_checksums_fail_before_dataset_download(published):
    client, settings, release, _ = published
    manifest = json.loads(client.objects[release["manifest_key"]])
    manifest["raw"]["key"] = "outside/raw.parquet"
    client.objects[release["manifest_key"]] = json_bytes(manifest)
    reader = SharedDatasetReader(settings, client=client)
    with pytest.raises(ValueError, match="checksum"):
        reader.read()
    with pytest.raises(ValueError, match="location"):
        reader.read(release["release_id"])
    assert not data_reads(client)


def test_schema_mismatch_and_no_stale_latest_fallback(published):
    client, settings, release, _ = published
    manifest = json.loads(client.objects[release["manifest_key"]])
    manifest["workable"]["columns"][0]["pandas_dtype"] = "int64"
    client.objects[release["manifest_key"]] = json_bytes(manifest)
    reader = SharedDatasetReader(settings, client=client)
    with pytest.raises(ValueError, match="schema"):
        reader.read(release["release_id"])
    assert not list(reader.cache.glob("*.parquet"))
    del client.objects["voc/datasets/latest.json"]
    with pytest.raises(LookupError, match="No shared"):
        reader.read()


def test_facade_cli_default_and_explicit_local(published, monkeypatch, tmp_path):
    from voc import datasets, shared_reader
    from voc.cli import main

    client, settings, release, _ = published
    factory = lambda: SharedDatasetReader(settings, client=client)
    monkeypatch.setattr(shared_reader, "SharedDatasetReader", factory)
    monkeypatch.setenv("VOC_DATASET_SOURCE", "s3")
    monkeypatch.setenv("AWS_BUCKET", settings.bucket)
    monkeypatch.setenv("AWS_REGION", settings.region)
    assert collector.get_dataset().info["release_id"] == release["release_id"]
    assert (
        collector.get_dataset(stage="raw", version=release["release_id"]).info["stage"]
        == "raw"
    )
    assert get_dataset(source="s3").info["release_id"] == release["release_id"]
    assert resolve(source="s3")["release_id"] == release["release_id"]
    for args in (["dataset-info", "--source", "s3"], ["summary"]):
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, result.output
        assert release["release_id"] in result.output
    monkeypatch.setattr(
        datasets, "resolve_local", lambda *a, **k: {"artifact_id": "personal"}
    )
    assert resolve(source="local")["artifact_id"] == "personal"
    for kwargs in ({"source": "invalid"}, {"source": "s3", "store": "raw.db"}):
        with pytest.raises(ValueError):
            collector.get_dataset(**kwargs)


def test_empty_runtime_diagnostic_in_fresh_python_process(published, tmp_path):
    import base64
    import os
    import subprocess
    import sys
    from pathlib import Path

    client, settings, release, _ = published
    fixture = tmp_path / "s3-fixture.json"
    fixture.write_text(
        json.dumps({k: base64.b64encode(v).decode() for k, v in client.objects.items()})
    )
    config = tmp_path / "storage.toml"
    config.write_text('dataset_source = "s3"\n')
    code = """
import base64, hashlib, io, json, os
from unittest.mock import patch
from click.testing import CliRunner
from src.mlops.check_shared_reader import main
objects = {k: base64.b64decode(v) for k,v in json.load(open(os.environ["FIXTURE"])).items()}
class S3:
    def get_object(self, **kwargs):
        data = objects[kwargs["Key"]]
        return {"Body": io.BytesIO(data), "ETag": hashlib.sha256(data).hexdigest()}
with patch("boto3.client", return_value=S3()):
    result = CliRunner().invoke(main)
    print(result.output)
    if result.exception:
        raise result.exception
"""
    env = os.environ | {
        "FIXTURE": str(fixture),
        "VOC_STORAGE_CONFIG": str(config),
        "VOC_DATA_DIR": str(tmp_path / "diagnostic-report"),
        "AWS_BUCKET": settings.bucket,
        "AWS_REGION": settings.region,
        "VOC_ARTIFACT_DESTINATION": "local",
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["release_id"] == release["release_id"]
    assert report["parquet_downloads"] == 2
    assert report["pinned_offline_read"]
    assert not report["publisher_metadata_required"]
