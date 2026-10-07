"""Read-only inspection, bounded scope selection and operator output contracts."""
# ruff: noqa: F811 -- pytest injects imported shared fixtures

import hashlib
import io
import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from botocore.exceptions import NoCredentialsError
from click.testing import CliRunner

from tests.integration.test_shared_publication import error
from tests.unit.test_embedding_release import (  # noqa: F401
    completed,
    manifest_of,
    outputs,
    release,
    runtime,
)
from tests.unit.test_storage_bootstrap import storage
from voc.embeddings.contracts import (
    DatasetIdentity,
    InputSelection,
    canonical_json,
    feature_identity,
)
from voc.embeddings.namespace import scope_prefix
from voc.embeddings.profiles import resolve_profile
from voc.storage.inspection import inspect_storage
from voc_dev.cli import main
from voc_dev.storage_render import render_report


class ReadOnlyS3:
    def __init__(self):
        self.objects, self.calls, self.errors = {}, [], {}

    def check(self, operation, key):
        self.calls.append((operation, key))
        failure = self.errors.get((operation, key))
        if failure:
            raise failure

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        self.check("list", Prefix)
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        start = int(ContinuationToken or 0)
        selected = keys[start : start + MaxKeys]
        truncated = start + MaxKeys < len(keys)
        result = {
            "Contents": [
                {
                    "Key": key,
                    "Size": len(self.objects[key]),
                    "LastModified": datetime(2026, 10, 7, tzinfo=UTC),
                    "StorageClass": "STANDARD",
                }
                for key in selected
            ],
            "IsTruncated": truncated,
        }
        if truncated:
            result["NextContinuationToken"] = str(start + MaxKeys)
        return result

    def get_object(self, *, Bucket, Key):
        self.check("get", Key)
        if Key not in self.objects:
            raise error("NoSuchKey")
        data = self.objects[Key]
        return {
            "Body": io.BytesIO(data),
            "ETag": '"' + hashlib.sha256(data).hexdigest() + '"',
        }

    def head_object(self, *, Bucket, Key):
        self.check("head", Key)
        if Key not in self.objects:
            raise error("404")
        return {"ContentLength": len(self.objects[Key])}

    def __getattr__(self, name):
        raise AssertionError("Unexpected S3 operation: " + name)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    configured = replace(storage(tmp_path, "local"), bucket="test-bucket")
    config = tmp_path / "storage.toml"
    config.write_text('artifact_destination = "s3"\ndataset_source = "s3"\n')
    for key, value in {
        "VOC_STORAGE_CONFIG": str(config),
        "VOC_DATA_DIR": str(tmp_path),
        "AWS_BUCKET": "test-bucket",
        "AWS_REGION": "us-east-2",
        "VOC_MEMBER_ID": "ness",
    }.items():
        monkeypatch.setenv(key, value)
    for key in (
        "VOC_EMBEDDINGS_PREFIX",
        "VOC_ARTIFACT_DESTINATION",
        "VOC_DATASET_SOURCE",
    ):
        monkeypatch.delenv(key, raising=False)
    return configured


@pytest.fixture
def client(monkeypatch):
    client = ReadOnlyS3()
    monkeypatch.setattr(
        "voc.storage.inspection.boto3.client", lambda *args, **kw: client
    )
    return client


def identity(settings):
    folder = settings.runtime_root / "storage"
    folder.mkdir()
    value = str(uuid4())
    (folder / "installation-id").write_text(value)
    return value


@pytest.fixture
def remote(release, settings, client):
    profile = resolve_profile()
    dataset = release["dataset_release_id"]
    prefix = scope_prefix(settings, profile.profile_id, dataset)
    manifest = manifest_of(release)
    manifest_key = f"{prefix}/releases/{release['embedding_release_id']}.json"
    client.objects[manifest_key] = (
        Path(release["directory"]) / "manifest.json"
    ).read_bytes()
    for name, filename in (("vectors", "vectors.npy"), ("reviews", "reviews.parquet")):
        client.objects[prefix + "/" + manifest["artifacts"][name]["key"]] = (
            Path(release["directory"]) / filename
        ).read_bytes()
    return (
        {
            "scope": "embeddings",
            "dataset_version": dataset,
            "release": release["embedding_release_id"],
        },
        prefix,
        manifest_key,
    )


def pointer(client, prefix, manifest_key, release_id):
    client.objects[prefix + "/latest.json"] = canonical_json(
        {
            "format_version": 1,
            "release_id": release_id,
            "manifest_key": manifest_key,
            "manifest_sha256": hashlib.sha256(client.objects[manifest_key]).hexdigest(),
        }
    )


def test_default_overview_only_lists_three_allowed_roots(settings, client):
    own = identity(settings)
    client.objects.update(
        {
            "voc/datasets/latest.json": b"{}",
            "voc/embeddings/profiles/a/datasets/b/release": b"data",
            f"members/ness/{own}/mlflow-artifacts/run/file": b"own",
            "members/another/id/secret": b"other",
        }
    )
    before = sorted(settings.runtime_root.rglob("*"))
    report = inspect_storage(settings, client=client)
    assert report["state"] == "ok"
    assert client.calls == [
        ("list", "voc/datasets/"),
        ("list", "voc/embeddings/"),
        ("list", f"members/ness/{own}/"),
    ]
    assert all(branch["listed_objects"] == 1 for branch in report["scopes"])
    tree = render_report(report)
    assert "[collapsed]" in tree and "another" not in tree
    assert "voc/" in tree and "members/" in tree and own in tree
    assert sorted(settings.runtime_root.rglob("*")) == before


def test_missing_identity_is_partial_not_broad_listing(settings, client):
    report = inspect_storage(settings, client=client)
    assert report["state"] == "partial"
    assert report["scopes"][-1]["status"] == "unconfigured"
    assert len(client.calls) == 2
    assert not (settings.runtime_root / "storage").exists()
    assert "unconfigured" in render_report(report)


@pytest.mark.parametrize("value", ["bad", "../someone", "a" * 257])
def test_corrupt_identity_never_lists_personal(settings, client, value):
    identity(settings)
    path = settings.runtime_root / "storage/installation-id"
    path.write_text(value)
    report = inspect_storage(settings, scope="personal", client=client)
    assert report["state"] == "error" and not client.calls
    assert path.read_text() == value


def test_custom_prefixes_empty_scopes_and_listing_limit(settings, client):
    settings = replace(
        settings,
        shared_prefix="shared/data",
        embeddings_prefix="shared/vectors",
        members_prefix="people",
    )
    own = identity(settings)
    prefix = f"people/ness/{own}/"
    for i in range(4):
        client.objects[prefix + f"run/{i}/object"] = b"abc"
    report = inspect_storage(settings, limit=2, client=client)
    branch = report["scopes"][-1]
    assert branch["listed_objects"] == 2 and branch["listed_bytes"] == 6
    assert branch["is_truncated"] and branch["next_continuation_token"] == "2"
    assert report["scopes"][0]["listed_objects"] == 0
    tree = render_report(report, depth=1)
    assert (
        "[empty page]" in tree
        and "[more objects available]" in tree
        and "[collapsed]" in tree
    )
    assert "[collapsed]" not in render_report(report, depth=4)
    assert len(client.calls) == 3
    continued = inspect_storage(
        settings, scope="personal", limit=2, continuation_token="2", client=client
    )
    assert not continued["scopes"][0]["is_truncated"]
    assert continued["scopes"][0]["objects"][0]["relative_key"] == "run/2/object"
    table = render_report(report, format="table")
    assert (
        "Relative key\tBytes\tModified\tStorage class" in table and "STANDARD" in table
    )


@pytest.mark.parametrize("failure", [error("AccessDenied"), NoCredentialsError()])
def test_remote_errors_preserve_accessible_branches(settings, client, failure):
    identity(settings)
    client.errors[("list", "voc/embeddings/")] = failure
    report = inspect_storage(settings, client=client)
    assert report["state"] == "partial"
    assert [branch["status"] for branch in report["scopes"]] == ["ok", "error", "ok"]
    assert report["scopes"][1]["listed_objects"] is None
    assert "ListObjectsV2" in render_report(report)


@pytest.mark.parametrize(
    "args",
    [
        ["--profile", "minilm-verbatim-v1"],
        ["--release", str(uuid4())],
        ["--dataset-version", str(uuid4())],
        ["--scope", "datasets", "--dataset-version", str(uuid4())],
        ["--scope", "embeddings", "--dataset-version", "latest"],
        [
            "--scope",
            "embeddings",
            "--dataset-version",
            str(uuid4()),
            "--profile",
            "unknown",
        ],
        [
            "--scope",
            "embeddings",
            "--dataset-version",
            str(uuid4()),
            "--release",
            "bad",
        ],
        ["--continuation-token", "token"],
        ["--format", "tree"],
        ["--limit", "1001"],
        ["--depth", "0"],
        ["--unknown"],
    ],
)
def test_invalid_cli_arguments_are_json_before_requests(settings, client, args):
    result = CliRunner().invoke(main, ["storage", "inspect", *args, "--json"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["state"] == "error", result.output
    assert not client.calls
    assert not (settings.runtime_root / "storage").exists()


def test_cli_tree_table_json_and_partial_exit(settings, client):
    partial = CliRunner().invoke(main, ["storage", "inspect", "--json"])
    assert partial.exit_code == 1 and json.loads(partial.stdout)["state"] == "partial"
    identity(settings)
    for extra in ([], ["--format", "table"], ["--json"]):
        result = CliRunner().invoke(main, ["storage", "inspect", *extra])
        assert result.exit_code == 0, result.output
        if extra == ["--json"]:
            assert json.loads(result.stdout)["state"] == "ok"
        else:
            assert (
                "test-bucket" in result.stdout and "Region: us-east-2" in result.stdout
            )


def test_release_metadata_is_independent_of_listing_limit(settings, client, remote):
    args, prefix, key = remote
    report = inspect_storage(settings, limit=1, client=client, **args)
    assert report["scopes"][0]["is_truncated"]
    detail = report["release"]
    assert detail["status"] == "metadata_consistent" and not detail["is_latest"]
    assert detail["latest_release_id"] is None
    assert not detail["artifact_content_verified"]
    assert len(detail["artifacts"]) == 2
    assert all(
        item["observed_bytes"] == item["declared_bytes"] for item in detail["artifacts"]
    )
    assert [key for op, key in client.calls if op == "get"] == [
        key,
        prefix + "/latest.json",
    ]


@pytest.mark.parametrize("matches", [True, False])
def test_matching_or_other_latest(settings, client, remote, matches):
    args, prefix, key = remote
    pointer(client, prefix, key, args["release"])
    if not matches:
        value = json.loads(client.objects[prefix + "/latest.json"])
        value["release_id"] = str(uuid4())
        value["manifest_key"] = prefix + "/releases/" + value["release_id"] + ".json"
        client.objects[prefix + "/latest.json"] = canonical_json(value)
    detail = inspect_storage(settings, client=client, **args)["release"]
    assert detail["status"] == "metadata_consistent"
    assert detail["is_latest"] is matches
    assert detail["latest_manifest_checksum_matches"] is (True if matches else None)


def test_valid_subset_without_latest(settings, client, remote):
    args, _, key = remote
    manifest = json.loads(client.objects[key])
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
    client.objects[key] = canonical_json(manifest)
    detail = inspect_storage(settings, client=client, **args)["release"]
    assert detail["status"] == "metadata_consistent"
    assert detail["selection"]["kind"] == "first-n"


@pytest.mark.parametrize(
    "damage,status",
    [
        ("missing-manifest", "incomplete"),
        ("missing-artifact", "incomplete"),
        ("size", "invalid"),
        ("malformed", "invalid"),
        ("oversize", "invalid"),
        ("dataset", "invalid"),
        ("profile", "invalid"),
        ("pointer-hash", "invalid"),
        ("pointer-null", "invalid"),
        ("denied-manifest", "error"),
        ("denied-artifact", "error"),
        ("denied-pointer", "error"),
    ],
)
def test_release_failure_classification(settings, client, remote, damage, status):
    args, prefix, key = remote
    manifest = json.loads(client.objects[key])
    artifact = prefix + "/" + manifest["artifacts"]["vectors"]["key"]
    if damage == "missing-manifest":
        del client.objects[key]
    elif damage == "missing-artifact":
        del client.objects[artifact]
    elif damage == "size":
        client.objects[artifact] = b"bad"
    elif damage == "malformed":
        client.objects[key] = b"{bad"
    elif damage == "oversize":
        client.objects[key] = b" " * (1024**2 + 1)
    elif damage in ("dataset", "profile"):
        if damage == "dataset":
            manifest["dataset"]["dataset_uri"] = "s3://test-bucket/other"
        else:
            manifest["profile"]["profile_id"] = "a" * 64
        client.objects[key] = canonical_json(manifest)
    elif damage == "pointer-hash":
        pointer(client, prefix, key, args["release"])
        client.objects[key] += b" "
    elif damage == "pointer-null":
        client.objects[prefix + "/latest.json"] = b"null"
    else:
        op, target = {
            "denied-manifest": ("get", key),
            "denied-artifact": ("head", artifact),
            "denied-pointer": ("get", prefix + "/latest.json"),
        }[damage]
        client.errors[(op, target)] = error("AccessDenied")
    report = inspect_storage(settings, client=client, **args)
    assert report["state"] != "ok" and report["release"]["status"] == status


def test_core_cli_has_no_model_or_tracking_imports(tmp_path):
    script = """
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'voc_ml', 'zenml', 'mlflow', 'torch', 'transformers', 'sentence_transformers'}:
            raise AssertionError('Unexpected import: ' + fullname)
sys.meta_path.insert(0, Block())
from voc_dev.cli import main
from click.testing import CliRunner
result = CliRunner().invoke(main, ['storage', 'inspect', '--help'])
assert result.exit_code == 0, result.exception
from pathlib import Path
from voc.models import StorageSettings
from voc.storage.inspection import inspect_storage
class ReadOnly:
    def list_objects_v2(self, **kwargs):
        return {"Contents": [], "IsTruncated": False}
settings = StorageSettings("local", "s3", Path.cwd(), "test-bucket", "us-east-2", "ness", "voc/datasets", "members", None)
report = inspect_storage(settings, scope="datasets", client=ReadOnly())
assert report["state"] == "ok"
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr


def test_listing_denial_still_checks_exact_release(settings, client, remote):
    args, prefix, _ = remote
    client.errors[("list", prefix + "/")] = error("AccessDenied")
    report = inspect_storage(settings, client=client, **args)
    assert report["state"] == "error"
    assert report["release"]["status"] == "metadata_consistent"


@pytest.mark.parametrize("payload", [b"{broken", b" " * (1024**2 + 1), b"[]"])
def test_invalid_pointer_metadata(settings, client, remote, payload):
    args, prefix, _ = remote
    client.objects[prefix + "/latest.json"] = payload
    report = inspect_storage(settings, client=client, **args)
    assert report["release"]["status"] == "invalid"


def test_cli_exact_release_json_failure_and_rendering(settings, client, remote):
    args, _, key = remote
    command = [
        "storage",
        "inspect",
        "--scope",
        "embeddings",
        "--dataset-version",
        args["dataset_version"],
        "--release",
        args["release"],
    ]
    readable = CliRunner().invoke(main, command)
    assert readable.exit_code == 0, readable.output
    assert "metadata_consistent" in readable.stdout
    assert "Artifact content verified: false" in readable.stdout
    assert "Selection: full" in readable.stdout
    del client.objects[key]
    result = CliRunner().invoke(main, [*command, "--json"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["release"]["status"] == "incomplete"


def test_client_initialization_error_is_structured(settings, monkeypatch):
    def unavailable(*args, **kwargs):
        raise NoCredentialsError()

    monkeypatch.setattr("voc.storage.inspection.boto3.client", unavailable)
    result = CliRunner().invoke(
        main, ["storage", "inspect", "--scope", "datasets", "--json"]
    )
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["scopes"][0]["error"]["code"] == "NoCredentialsError"


def test_terminal_keys_escape_control_characters(settings, client):
    client.objects["voc/datasets/evil\x1b[31m\nkey"] = b"text"
    report = inspect_storage(settings, scope="datasets", client=client)
    for output in (render_report(report), render_report(report, format="table")):
        assert "\x1b" not in output and "\\nkey" in output


def test_same_size_artifact_change_is_outside_metadata_check(settings, client, remote):
    args, prefix, key = remote
    manifest = json.loads(client.objects[key])
    artifact_key = prefix + "/" + manifest["artifacts"]["vectors"]["key"]
    client.objects[artifact_key] = b"x" * len(client.objects[artifact_key])
    detail = inspect_storage(settings, client=client, **args)["release"]
    assert detail["status"] == "metadata_consistent"
    assert detail["artifact_content_verified"] is False
