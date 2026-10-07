"""Bounded, read-only S3 inventory and embedding release metadata inspection."""

import hashlib
import json
import re
from contextlib import closing
from datetime import UTC, datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from voc.embeddings.contracts import require_uuid
from voc.embeddings.manifest import (
    MAX_JSON_BYTES,
    require_promotable,
    validate_manifest,
)
from voc.embeddings.namespace import scope_prefix
from voc.embeddings.profiles import resolve_profile

from .objects import ReleaseObjects, _missing
from .settings import read_installation_id, validate_prefixes


def validate_selectors(
    scope, dataset_version, profile, release, limit, continuation_token
):
    """Validate before creating an AWS client or issuing any requests."""
    if scope not in ("all", "embeddings", "datasets", "personal"):
        raise ValueError("Unknown storage scope")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    if scope != "embeddings" and any(
        value is not None for value in (dataset_version, profile, release)
    ):
        raise ValueError("Dataset/profile/release selectors require --scope embeddings")
    if (profile is not None or release is not None) and dataset_version is None:
        raise ValueError("--profile and --release require --dataset-version")
    if dataset_version is not None:
        require_uuid(dataset_version, "dataset_version")
        resolve_profile(profile or "minilm-verbatim-v1")
    if release is not None:
        require_uuid(release, "embedding_release_id")
    if continuation_token is not None and scope == "all":
        raise ValueError("--continuation-token requires a single explicit scope")


def _error(error, operation):
    # Avoid including raw SDK messages (which may contain signed URLs or other
    # credential-bearing request context). Keep actionable operation/code details.
    code = (
        error.response["Error"]["Code"]
        if isinstance(error, ClientError)
        else type(error).__name__
    )
    return {
        "operation": operation,
        "code": code,
        "message": f"{operation} failed ({code})",
    }


def _timestamp(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _manifest_bytes(client, bucket, key):
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if _missing(error):
            return None
        raise
    with closing(response["Body"]) as body:
        data = body.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("Manifest exceeds metadata size limit")
    return data


def inspect_release(client, storage, prefix, dataset_version, profile, release):
    """Read at most two small JSON objects and HEAD the two declared artifacts."""
    objects = ReleaseObjects(client, storage.bucket, prefix)
    key = objects.key(f"releases/{release}.json")
    result = {
        "embedding_release_id": release,
        "dataset_release_id": dataset_version,
        "profile_id": profile.profile_id,
        "manifest_uri": f"s3://{storage.bucket}/{key}",
        "manifest_sha256": None,
        "coverage": None,
        "selection": None,
        "artifacts": [],
        "latest_release_id": None,
        "is_latest": None,
        "latest_manifest_checksum_matches": None,
        "artifact_content_verified": False,
        "issues": [],
    }

    def issue(status, operation, message):
        result["issues"].append(
            {"status": status, "operation": operation, "message": message}
        )

    manifest = None
    try:
        data = _manifest_bytes(client, storage.bucket, key)
        if data is None:
            issue("incomplete", "GetObject manifest", "Manifest is missing")
        else:
            result["manifest_sha256"] = hashlib.sha256(data).hexdigest()
            candidate = json.loads(data)
            validate_manifest(candidate)
            if (
                candidate["embedding_release_id"] != release
                or candidate["dataset"]["release_id"] != dataset_version
                or candidate["dataset"]["dataset_uri"] != storage.shared_dataset_uri
                or candidate["profile"]["profile_id"] != profile.profile_id
            ):
                raise ValueError(
                    "Manifest differs from the requested dataset/profile/release scope"
                )
            manifest = candidate
            result.update(
                coverage=manifest["coverage"], selection=manifest["selection"]
            )
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        issue("invalid", "GetObject manifest", str(error))
    except (BotoCoreError, ClientError, OSError) as error:
        issue(
            "error",
            "GetObject manifest",
            _error(error, "GetObject manifest")["message"],
        )

    if manifest is not None:
        for name, artifact in manifest["artifacts"].items():
            key = objects.key(artifact["key"])
            item = {
                "name": name,
                "key": key,
                "uri": f"s3://{storage.bucket}/{key}",
                "declared_bytes": artifact["size_bytes"],
                "observed_bytes": None,
                "present": None,
            }
            result["artifacts"].append(item)
            try:
                head = client.head_object(Bucket=storage.bucket, Key=key)
                item.update(present=True, observed_bytes=head["ContentLength"])
                if head["ContentLength"] != artifact["size_bytes"]:
                    issue(
                        "invalid",
                        f"HeadObject {name}",
                        "Object size differs from manifest",
                    )
            except ClientError as error:
                if _missing(error):
                    item["present"] = False
                    issue("incomplete", f"HeadObject {name}", "Object is missing")
                else:
                    issue(
                        "error",
                        f"HeadObject {name}",
                        _error(error, f"HeadObject {name}")["message"],
                    )
            except (BotoCoreError, OSError) as error:
                issue(
                    "error",
                    f"HeadObject {name}",
                    _error(error, f"HeadObject {name}")["message"],
                )
    try:
        pointer, etag = objects.latest()
        if pointer is None and etag is not None:
            raise ValueError("Latest pointer must be an object, not null")
        result["is_latest"] = pointer is not None and pointer["release_id"] == release
        result["latest_release_id"] = pointer["release_id"] if pointer else None
        if result["is_latest"] and result["manifest_sha256"]:
            matches = pointer["manifest_sha256"] == result["manifest_sha256"]
            result["latest_manifest_checksum_matches"] = matches
            if not matches:
                issue(
                    "invalid", "GetObject latest", "Pointer manifest checksum differs"
                )
            if manifest is not None:
                require_promotable(manifest)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        issue("invalid", "GetObject latest", str(error))
    except (BotoCoreError, ClientError, OSError) as error:
        issue("error", "GetObject latest", _error(error, "GetObject latest")["message"])
    statuses = {item["status"] for item in result["issues"]}
    result["status"] = next(
        (status for status in ("error", "invalid", "incomplete") if status in statuses),
        "metadata_consistent",
    )
    return result


def inspect_storage(
    storage,
    *,
    scope="all",
    dataset_version=None,
    profile=None,
    release=None,
    limit=100,
    continuation_token=None,
    client=None,
):
    """Return a report from one recursive listing page per configured scope.

    No artifact downloads, identity creation, producer imports or S3 writes.
    Counts describe this page, never a complete bucket or prefix inventory.
    """
    validate_selectors(
        scope, dataset_version, profile, release, limit, continuation_token
    )
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", storage.bucket):
        raise ValueError("Set AWS_BUCKET to a bucket name for inspection")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", storage.region):
        raise ValueError("Set AWS_REGION for inspection")
    datasets, members, embeddings = validate_prefixes(
        storage.shared_prefix, storage.members_prefix, storage.embeddings_prefix
    )
    resolved_profile = (
        resolve_profile(profile or "minilm-verbatim-v1") if dataset_version else None
    )
    if resolved_profile:
        embeddings = scope_prefix(storage, resolved_profile.profile_id, dataset_version)
    report = {
        "schema_version": "voc-storage-inspection-v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "bucket": storage.bucket,
        "region": storage.region,
        "limit_per_scope": limit,
        "scopes": [],
        "release": None,
    }
    names = ("datasets", "embeddings", "personal") if scope == "all" else (scope,)
    for name in names:
        branch = {
            "name": name,
            "prefix": None,
            "uri": None,
            "status": "ok",
            "objects": [],
            "listed_objects": None,
            "listed_bytes": None,
            "is_truncated": None,
            "next_continuation_token": None,
        }
        report["scopes"].append(branch)
        prefix = {"datasets": datasets, "embeddings": embeddings}.get(name)
        if name == "personal":
            try:
                identity = read_installation_id(storage.runtime_root)
                if not storage.member_id or identity is None:
                    branch.update(
                        status="unconfigured",
                        error={
                            "message": "Personal scope needs a configured member and existing installation ID"
                        },
                    )
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_-]+", storage.member_id):
                    raise ValueError("Invalid configured member ID")
                prefix = f"{members}/{storage.member_id}/{identity}"
            except (ValueError, OSError) as error:
                branch.update(status="error", error={"message": str(error)})
                continue
        branch.update(prefix=prefix + "/", uri=f"s3://{storage.bucket}/{prefix}/")
        try:
            if client is None:
                client = boto3.client("s3", region_name=storage.region)
            request = {
                "Bucket": storage.bucket,
                "Prefix": prefix + "/",
                "MaxKeys": limit,
            }
            if continuation_token is not None:
                request["ContinuationToken"] = continuation_token
            page = client.list_objects_v2(**request)
            records = []
            for item in page.get("Contents", []):
                key = item["Key"]
                if not key.startswith(prefix + "/"):
                    raise ValueError(
                        "Listing returned a key outside its requested prefix"
                    )
                records.append(
                    {
                        "key": key,
                        "relative_key": key[len(prefix) + 1 :],
                        "uri": f"s3://{storage.bucket}/{key}",
                        "bytes": item["Size"],
                        "last_modified": _timestamp(item.get("LastModified")),
                        "storage_class": item.get("StorageClass", "STANDARD"),
                    }
                )
            branch.update(
                objects=records,
                listed_objects=len(records),
                listed_bytes=sum(item["bytes"] for item in records),
                is_truncated=page.get("IsTruncated", False),
                next_continuation_token=page.get("NextContinuationToken"),
            )
        except (
            BotoCoreError,
            ClientError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            branch.update(status="error", error=_error(error, "ListObjectsV2"))
        # Exact release inspection is independent of inventory pagination or failure.
        if release is not None and client is not None:
            report["release"] = inspect_release(
                client, storage, prefix, dataset_version, resolved_profile, release
            )
    successful = sum(branch["status"] == "ok" for branch in report["scopes"])
    release_ok = release is None or (
        report["release"] is not None
        and report["release"]["status"] == "metadata_consistent"
    )
    report["state"] = (
        "ok"
        if successful == len(names) and release_ok
        else ("partial" if successful else "error")
    )
    return report
