"""Coordinate read-only scope inventory and exact release inspection."""

import re
from datetime import UTC, datetime
from typing import cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from voc.embeddings.shared.namespace import scope_prefix
from voc.embeddings.shared.profiles import resolve_profile

from ..shared.settings import read_installation_id, validate_prefixes
from .contracts import (
    ReleaseInspection,
    ScopeInspection,
    ScopeName,
    StorageInspection,
    validate_selectors,
)
from .reader import _error, list_scope, read_release_metadata


def inspect_release(
    client, storage, prefix, dataset_version, profile, release
) -> ReleaseInspection:
    """Read at most two small JSON objects and HEAD the two declared artifacts."""
    result = cast(
        ReleaseInspection,
        read_release_metadata(client, storage, prefix, dataset_version, profile, release),
    )
    statuses = {item["status"] for item in result["issues"]}
    if "error" in statuses:
        result["status"] = "error"
    elif "invalid" in statuses:
        result["status"] = "invalid"
    elif "incomplete" in statuses:
        result["status"] = "incomplete"
    else:
        result["status"] = "metadata_consistent"
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
) -> StorageInspection:
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
    report: StorageInspection = {
        "schema_version": "voc-storage-inspection-v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "bucket": storage.bucket,
        "region": storage.region,
        "limit_per_scope": limit,
        "scopes": [],
        "release": None,
        "state": "error",
    }
    names: tuple[ScopeName, ...] = (
        ("datasets", "embeddings", "personal")
        if scope == "all"
        else (cast(ScopeName, scope),)
    )
    for name in names:
        branch: ScopeInspection = {
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
        else:
            prefix = datasets if name == "datasets" else embeddings
        branch.update(prefix=prefix + "/", uri=f"s3://{storage.bucket}/{prefix}/")
        try:
            if client is None:
                client = boto3.client("s3", region_name=storage.region)
            page = list_scope(client, storage.bucket, prefix, limit, continuation_token)
            branch.update(
                objects=page["objects"],
                listed_objects=page["listed_objects"],
                listed_bytes=page["listed_bytes"],
                is_truncated=page["is_truncated"],
                next_continuation_token=page["next_continuation_token"],
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
