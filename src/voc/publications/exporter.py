"""Describe existing datasets without invoking collection or model pipelines."""

import pandas as pd

from voc.datasets import summary
from voc.datasets.manifest import canonical_id
from voc.models import Snapshot

from .contracts import utc_datetime, validate_description


def _identity(info):
    # Personal catalog entries predate the explicit source field.
    source = info.get(
        "source",
        "personal",
    )
    if source == "local":
        source = "personal"
    if (
        info.get(
            "stage",
            "prepared",
        )
        != "prepared"
    ):
        raise ValueError("Publication requires a prepared dataset")

    # Namespace native IDs so an artifact cannot be mistaken for an S3 release.
    if source in ("personal", "s3"):
        if info.get("schema_version") != "workable-v1":
            raise ValueError("Unsupported prepared dataset schema")
        field = "release_id" if source == "s3" else "artifact_id"
        version = canonical_id(info.get(field))
        return source, version, f"{source}-{version}"
    if source == "fixture":
        return source, info.get("source_version"), info.get("dataset_id")
    raise ValueError("Dataset source must be personal, s3 or fixture")


def _platform(value):
    # Retain missing platform rows in an explicit category before counting.
    if pd.isna(value) or (
        isinstance(
            value,
            str,
        )
        and not value.strip()
    ):
        return "unknown"
    if not isinstance(
        value,
        str,
    ):
        raise ValueError("Platform must be text or missing")  # noqa: TRY004 - public contract error
    return value.strip()


def describe_dataset(snapshot):
    """Return public aggregates for one existing Snapshot, without changing it.

    Real snapshots provide no verified collection or embedding evidence through
    this adapter yet. Missing information stays unknown. Fixture identities are
    accepted for Python tests only; the public CLI accepts personal and S3 IDs.
    """
    # Check the declared size against the rows actually loaded by the reader.
    frame, info = snapshot.data, snapshot.info
    if not isinstance(
        info,
        dict,
    ) or not isinstance(
        frame,
        pd.DataFrame,
    ):
        raise ValueError("Expected a dataset Snapshot with metadata and a DataFrame")  # noqa: TRY004 - public contract error
    if not frame.columns.is_unique or not {"platform", "record_date"} <= set(frame):
        raise ValueError("Dataset requires unique platform and record_date columns")
    if type(info.get("row_count")) is not int or info["row_count"] != len(frame):
        raise ValueError("Dataset row_count differs from the loaded records")
    source, version, dataset_id = _identity(info)

    # Only these two columns are needed; review bodies and private metadata stay out.
    public_frame = frame.loc[:, ["platform", "record_date"]].copy()
    public_frame["platform"] = public_frame["platform"].map(_platform)
    dates = pd.to_datetime(
        public_frame["record_date"],
        errors="coerce",
        utc=True,
        format="mixed",
    )
    public_frame["record_date"] = dates
    counts = summary(
        Snapshot(
            public_frame,
            {},
        )
    )

    # Publication/upload time is not preparation time or collection time.
    prepared_at = info.get("prepared_at")
    if prepared_at is not None:
        prepared_at = utc_datetime(
            prepared_at,
            "prepared_at",
        )
    known_dates = dates.dropna()
    reference = "dataset-release" if source == "s3" else "dataset-artifact"
    provenance = (
        []
        if source == "fixture"
        else [
            {
                "evidence_id": "dataset-origin",
                "dataset_id": dataset_id,
                "kind": "dataset_origin",
                "evidence_ref": f"{reference}:{version}",
                "description": "Existing prepared dataset selected for this publication.",
            }
        ]
    )

    # Assemble exactly the fields accepted by the publication contract.
    description = {
        "dataset": {
            "dataset_id": dataset_id,
            "source_kind": source,
            "source_version": version,
            "row_count": len(frame),
            "unknown_dates": counts["unknown_dates"],
            "record_start": known_dates.min().to_pydatetime()
            if len(known_dates)
            else None,
            "record_end": known_dates.max().to_pydatetime()
            if len(known_dates)
            else None,
            "prepared_at": prepared_at,
        },
        "provenance": provenance,
        "sources": [
            {
                "dataset_id": dataset_id,
                "platform": platform,
                "record_count": int(count),
                "unknown_dates": int(
                    (public_frame["platform"].eq(platform) & dates.isna()).sum()
                ),
                "collection_state": "unknown",
                "latest_collection_at": None,
                "collection_evidence_id": None,
            }
            for platform, count in sorted(counts["platform_counts"].items())
        ],
        "monthly_counts": [
            {"dataset_id": dataset_id, "month": month, "record_count": int(count)}
            for month, count in counts["month_counts"].items()
        ],
        "embedding_status": {
            "dataset_id": dataset_id,
            "state": "not_reported",
            "release_id": None,
            "model": None,
            "covered_rows": None,
            "verification_evidence_id": None,
        },
    }
    validate_description(description)
    return description
