"""Failure injection for sharing: no network or credentials used."""

import base64
import hashlib
import io
import json
import uuid
from dataclasses import replace

import boto3
import pandas as pd
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber
from test_storage_bootstrap import storage

from src.preparation.dataset import prepare_dataset
from voc.shared import PublicationConflict, ReleaseObjects, frame_hash, publish_release
from voc.store import Snapshot


def error(code):
    return ClientError({"Error": {"Code": code, "Message": "injected"}}, "S3")


class MemoryS3:
    def __init__(self):
        self.objects, self.writes = {}, []
        self.fail_suffix = self.lose_suffix = None

    def get_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise error("NoSuchKey")
        data = self.objects[Key]
        return {
            "Body": io.BytesIO(data),
            "ETag": '"' + hashlib.sha256(data).hexdigest() + '"',
        }

    def put_object(
        self, *, Bucket, Key, Body, IfNoneMatch=None, IfMatch=None, **kwargs
    ):
        if self.fail_suffix and Key.endswith(self.fail_suffix):
            raise OSError("simulated interruption")
        old = self.objects.get(Key)
        if IfNoneMatch == "*" and old is not None:
            raise error("PreconditionFailed")
        if IfMatch and (
            old is None or IfMatch != '"' + hashlib.sha256(old).hexdigest() + '"'
        ):
            raise error("PreconditionFailed")
        data = Body.read() if hasattr(Body, "read") else Body
        if "ChecksumSHA256" in kwargs:
            assert (
                kwargs["ChecksumSHA256"]
                == base64.b64encode(hashlib.sha256(data).digest()).decode()
            )
        self.objects[Key] = data
        self.writes.append(Key)
        if self.lose_suffix and Key.endswith(self.lose_suffix):
            self.lose_suffix = None
            raise OSError("simulated lost response")
        return {}


@pytest.fixture
def pair(store):
    raw = store.read()
    prepared = prepare_dataset(raw.data)
    info = {
        "run_id": str(uuid.uuid4()),
        "artifact_id": str(uuid.uuid4()),
        "raw_revision_id": raw.info["revision_id"],
        "raw_store_id": raw.info["raw_store_id"],
        "row_count": len(prepared.data),
        "schema_version": "workable-v1",
        "content_sha256": frame_hash(prepared.data),
        "rules": prepared.report["rules"],
        "project_identity": "fixture-code",
        "mlflow_run_id": uuid.uuid4().hex,
    }
    return raw, Snapshot(prepared.data, info)


def another(workable):
    return Snapshot(
        workable.data.copy(),
        workable.info | {"run_id": str(uuid.uuid4()), "artifact_id": str(uuid.uuid4())},
    )


def test_complete_pair_roundtrip_raw_reuse_and_old_retry_conflict(tmp_path, pair):
    raw, workable = pair
    client, settings = MemoryS3(), storage(tmp_path, "s3")
    first = publish_release(raw, workable, settings, client=client)
    assert publish_release(raw, workable, settings, client=client) == first
    second = publish_release(raw, another(workable), settings, client=client)
    assert first["release_id"] != second["release_id"]
    raw_key = f"voc/datasets/raw/{raw.info['revision_id']}/dataset.parquet"
    assert client.writes.count(raw_key) == 1
    pd.testing.assert_frame_equal(
        pd.read_parquet(io.BytesIO(client.objects[raw_key])), raw.data
    )
    manifest = json.loads(client.objects[second["manifest_key"]])
    pd.testing.assert_frame_equal(
        pd.read_parquet(io.BytesIO(client.objects[manifest["workable"]["key"]])),
        workable.data,
    )
    assert "raw_store_id" not in json.dumps(manifest)
    with pytest.raises(PublicationConflict):
        publish_release(raw, workable, settings, client=client)
    assert (
        json.loads(client.objects["voc/datasets/latest.json"])["release_id"]
        == second["release_id"]
    )


@pytest.mark.parametrize("failure", ["raw", "workable", "manifest", "latest"])
def test_interrupted_attempt_retains_identity_and_previous_latest(
    tmp_path, pair, failure
):
    raw, workable = pair
    client, settings = MemoryS3(), storage(tmp_path, "s3")
    if failure == "raw":
        client.fail_suffix = f"raw/{raw.info['revision_id']}/dataset.parquet"
    elif failure == "workable":
        client.fail_suffix = f"workable/{workable.info['artifact_id']}/dataset.parquet"
    else:
        client.fail_suffix = ".json" if failure == "manifest" else "latest.json"
    with pytest.raises(OSError):
        publish_release(raw, workable, settings, client=client)
    assert "voc/datasets/latest.json" not in client.objects
    folders = [
        p.name for p in (tmp_path / "shared-publications").iterdir() if p.is_dir()
    ]
    client.fail_suffix = None
    result = publish_release(raw, workable, settings, client=client)
    assert folders == [result["release_id"]]


def test_lost_promotion_response_is_idempotent(tmp_path, pair):
    client, settings = MemoryS3(), storage(tmp_path, "s3")
    client.lose_suffix = "latest.json"
    with pytest.raises(OSError):
        publish_release(*pair, settings, client=client)
    before = client.objects["voc/datasets/latest.json"]
    result = publish_release(*pair, settings, client=client)
    assert result["release_id"] == json.loads(before)["release_id"]
    assert client.writes.count("voc/datasets/latest.json") == 1


def test_intervening_publisher_cannot_be_overwritten_by_retry(tmp_path, pair):
    raw, workable = pair
    client, settings = MemoryS3(), storage(tmp_path, "s3")
    first = publish_release(raw, workable, settings, client=client)
    delayed = another(workable)
    client.fail_suffix = "latest.json"
    with pytest.raises(OSError):
        publish_release(raw, delayed, settings, client=client)
    assert (
        json.loads(client.objects["voc/datasets/latest.json"])["release_id"]
        == first["release_id"]
    )
    client.fail_suffix = None
    other = replace(settings, runtime_root=tmp_path / "other-installation")
    newer = publish_release(raw, another(workable), other, client=client)
    with pytest.raises(PublicationConflict):
        publish_release(raw, delayed, settings, client=client)
    assert (
        json.loads(client.objects["voc/datasets/latest.json"])["release_id"]
        == newer["release_id"]
    )


def test_corrupt_immutable_object_cannot_be_overwritten(tmp_path, pair):
    raw, workable = pair
    client, settings = MemoryS3(), storage(tmp_path, "s3")
    key = f"voc/datasets/raw/{raw.info['revision_id']}/dataset.parquet"
    client.objects[key] = b"corrupt"
    with pytest.raises(ValueError, match="checksum"):
        publish_release(raw, workable, settings, client=client)
    assert client.objects[key] == b"corrupt"
    assert "voc/datasets/latest.json" not in client.objects


def test_mismatched_raw_or_workable_fails_before_s3(tmp_path, pair):
    raw, workable = pair
    client, settings = MemoryS3(), storage(tmp_path, "s3")
    with pytest.raises(ValueError, match="lineage"):
        publish_release(
            Snapshot(raw.data, raw.info | {"revision_id": str(uuid.uuid4())}),
            workable,
            settings,
            client=client,
        )
    wrong = workable.data.copy()
    wrong.loc[0, "text"] = "changed"
    with pytest.raises(ValueError, match="differs"):
        publish_release(raw, Snapshot(wrong, workable.info), settings, client=client)
    assert not client.objects


def test_changed_retry_inputs_and_unsafe_keys_rejected(tmp_path, pair):
    raw, workable = pair
    client, settings = MemoryS3(), storage(tmp_path, "s3")
    publish_release(raw, workable, settings, client=client)
    with pytest.raises(ValueError, match="inputs differ"):
        publish_release(
            raw,
            Snapshot(workable.data, workable.info | {"project_identity": "changed"}),
            settings,
            client=client,
        )
    objects = ReleaseObjects(client, settings.bucket, settings.shared_prefix)
    with pytest.raises(ValueError):
        objects.key("../outside")
    client.objects["voc/datasets/latest.json"] = json.dumps(
        {
            "release_id": str(uuid.uuid4()),
            "format_version": 1,
            "manifest_key": "outside",
            "manifest_sha256": "0" * 64,
        }
    ).encode()
    with pytest.raises(ValueError, match="Invalid shared"):
        objects.latest()


def test_boto_contract_preserves_conditional_write_and_checksums(tmp_path):
    client = boto3.client(
        "s3",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    file = tmp_path / "data"
    file.write_bytes(b"example")
    digest = hashlib.sha256(b"example").hexdigest()
    with Stubber(client) as stub:
        stub.add_client_error(
            "get_object",
            service_error_code="NoSuchKey",
            expected_params={"Bucket": "test-bucket", "Key": "test/data"},
        )
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "test-bucket",
                "Key": "test/data",
                "Body": ANY,
                "IfNoneMatch": "*",
                "ContentLength": 7,
                "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
            },
        )
        stub.add_response(
            "get_object",
            {"Body": io.BytesIO(b"example")},
            {"Bucket": "test-bucket", "Key": "test/data"},
        )
        ReleaseObjects(client, "test-bucket", "test").put_immutable("data", file)
        stub.assert_no_pending_responses()


def test_diagnostic_is_personal_and_reload_never_writes(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from src.mlops import check_shared

    settings = storage(tmp_path, "s3")
    client = MemoryS3()
    monkeypatch.setattr(check_shared, "load_storage", lambda **kwargs: settings)
    monkeypatch.setattr(check_shared.boto3, "client", lambda *args, **kwargs: client)
    result = CliRunner().invoke(check_shared.main)
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["raw_reused"] and report["stale_retry_rejected"]
    assert all(
        key.startswith("members/ness/" + settings.installation_id + "/tests/shared/")
        for key in client.objects
    )
    writes = list(client.writes)
    again = CliRunner().invoke(check_shared.main, ["--reload"])
    assert again.exit_code == 0, again.output
    assert json.loads(again.output) == report
    assert client.writes == writes


def test_share_runner_requires_matching_local_publication_and_pins_raw(
    pair, monkeypatch
):
    from unittest.mock import Mock

    from src.mlops import runner
    from voc import datasets, shared
    from voc import storage as storage_module

    raw, workable = pair
    monkeypatch.setattr(
        runner, "validated_run", lambda _: (workable.info, workable.data)
    )
    monkeypatch.setattr(datasets, "resolve", lambda _: workable.info)
    mock_store = Mock()
    mock_store.read.return_value = raw
    monkeypatch.setattr(runner, "RawStore", lambda _: mock_store)
    monkeypatch.setattr(storage_module, "load_storage", lambda: "settings")
    publish = Mock(return_value={"status": "published"})
    monkeypatch.setattr(shared, "publish_release", publish)
    assert runner.share_run(workable.info["run_id"]) == {"status": "published"}
    mock_store.read.assert_called_once_with(workable.info["raw_revision_id"])
    assert publish.call_args.args[0] is raw
    monkeypatch.setattr(
        datasets, "resolve", lambda _: workable.info | {"row_count": -1}
    )
    with pytest.raises(ValueError, match="Local publication differs"):
        runner.share_run(workable.info["run_id"])
    assert publish.call_count == 1
