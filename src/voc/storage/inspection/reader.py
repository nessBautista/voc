"""Bounded S3 listing and release metadata reads, without artifact downloads."""

import hashlib
import json
from contextlib import closing

from botocore.exceptions import BotoCoreError, ClientError

from voc.embeddings.shared.manifest import (
    MAX_JSON_BYTES,
    require_promotable,
    validate_manifest,
)

from ..shared.objects import ReleaseObjects, _missing
from .contracts import (
    ArtifactInspection,
    InspectionError,
    ListingPage,
    ObjectRecord,
    ReleaseMetadata,
)


def _error(error, operation) -> InspectionError:
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


def read_release_metadata(
    client, storage, prefix, dataset_version, profile, release
) -> ReleaseMetadata:
    """Read at most two small JSON objects and HEAD the two declared artifacts."""
    objects = ReleaseObjects(client, storage.bucket, prefix)
    key = objects.key(f"releases/{release}.json")
    result: ReleaseMetadata = {
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
            item: ArtifactInspection = {
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
        if pointer is not None and result["is_latest"] and result["manifest_sha256"]:
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
    return result


def list_scope(client, bucket, prefix, limit, continuation_token) -> ListingPage:
    """Read exactly one recursive listing page under the requested scope."""
    request = {
        "Bucket": bucket,
        "Prefix": prefix + "/",
        "MaxKeys": limit,
    }
    if continuation_token is not None:
        request["ContinuationToken"] = continuation_token
    page = client.list_objects_v2(**request)
    records: list[ObjectRecord] = []
    for item in page.get("Contents", []):
        key = item["Key"]
        if not key.startswith(prefix + "/"):
            raise ValueError("Listing returned a key outside its requested prefix")
        records.append(
            {
                "key": key,
                "relative_key": key[len(prefix) + 1 :],
                "uri": f"s3://{bucket}/{key}",
                "bytes": item["Size"],
                "last_modified": _timestamp(item.get("LastModified")),
                "storage_class": item.get("StorageClass", "STANDARD"),
            }
        )
    return {
        "objects": records,
        "listed_objects": len(records),
        "listed_bytes": sum(item["bytes"] for item in records),
        "is_truncated": page.get("IsTruncated", False),
        "next_continuation_token": page.get("NextContinuationToken"),
    }
