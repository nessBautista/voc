"""Describe existing datasets and analysis candidates without running any model."""

import json

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


def describe_analysis(stored, description):
    """Extend a dataset description with one stored analysis candidate.

    ``stored`` is a verified ``voc.analysis.store.StoredAnalysis``. The result
    is a v2 description: the candidate's reviews, assignments, topics,
    sentiment and interpretations, with the embedding release registered and
    the candidate cited as provenance. The input description is not modified.
    """
    from copy import deepcopy

    from voc.analysis.store import analysis_directory
    from voc.embeddings.contracts import canonical_json

    from .store import file_sha256

    description = deepcopy(description)
    dataset = description["dataset"]
    manifest = stored.manifest
    inputs = manifest.inputs
    if (
        dataset["source_kind"] != "s3"
        or inputs["dataset"]["release_id"] != dataset["source_version"]
    ):
        raise ValueError("The analysis was built from another dataset release")
    if inputs["dataset"]["row_count"] != dataset["row_count"]:
        raise ValueError("The analysis dataset row count differs from the description")
    dataset_id = dataset["dataset_id"]
    embedding = inputs["embedding"]
    selection = inputs["selection"]
    coverage = manifest.coverage

    # The candidate proves which embedding release the dataset has; register it.
    description["embedding_status"] = {
        "dataset_id": dataset_id,
        "state": "registered",
        "release_id": embedding["release_id"],
        "model": embedding["profile_name"],
        "covered_rows": embedding["row_count"],
        "verification_evidence_id": None,
    }
    description["provenance"] = [
        *description["provenance"],
        {
            "evidence_id": "embedding-registration",
            "dataset_id": dataset_id,
            "kind": "embedding_registration",
            "evidence_ref": f"embedding-release:{embedding['release_id']}",
            "description": "Embedding release the analysis candidate was computed on.",
        },
        {
            "evidence_id": "analysis-candidate",
            "dataset_id": dataset_id,
            "kind": "analysis_candidate",
            "evidence_ref": f"analysis-candidate:{manifest.analysis_id}",
            "description": "Immutable Operator candidate whose results this package carries.",
        },
    ]
    frame = stored.reviews
    reviews = [
        {
            "dataset_id": dataset_id,
            "record_id": record_id,
            "platform": _platform(platform),
            "record_date": None if pd.isna(record_date) else str(record_date),
            "model_text": text,
            "vector_row": int(position),
        }
        for record_id, platform, record_date, text, position in zip(
            frame["record_id"],
            frame["platform"],
            frame["record_date"],
            frame["model_text"],
            frame["vector_row"],
            strict=True,
        )
    ]
    members = {}
    for assignment in stored.assignments:
        members[assignment.topic_id] = members.get(assignment.topic_id, 0) + 1
    interpreted = {record["topic_id"]: record for record in stored.interpretations}
    description["analysis"] = {
        "analysis": {
            "dataset_id": dataset_id,
            "analysis_id": manifest.analysis_id,
            "parent_analysis_id": manifest.parent_analysis_id,
            "embedding_release_id": embedding["release_id"],
            "embedding_profile": embedding["profile_name"],
            "selection_kind": selection["kind"],
            "selection_size": selection["size"],
            "selected_reviews": coverage["selected_reviews"],
            "assigned_reviews": coverage["assigned_reviews"],
            "unassigned_reviews": coverage["unassigned_reviews"],
            "topic_count": coverage["topic_count"],
            "classified": coverage["classified"],
            "excluded": coverage["excluded"],
            "failed": coverage["failed"],
            "configuration_json": canonical_json(manifest.configuration).decode(),
            "code_sha256": manifest.code_sha256,
            "manifest_sha256": file_sha256(
                analysis_directory(manifest.analysis_id) / "manifest.json"
            ),
            "created_at": manifest.created_at,
        },
        "reviews": reviews,
        "assignments": [
            {
                "dataset_id": dataset_id,
                "record_id": a.record_id,
                "topic_id": a.topic_id,
                "membership_strength": a.membership_strength,
            }
            for a in stored.assignments
        ],
        "topics": [
            {
                "dataset_id": dataset_id,
                "topic_id": t.topic_id,
                "name": t.name,
                "description": t.description,
                "keywords_json": json.dumps(
                    [{"term": k.term, "weight": k.weight} for k in t.keywords],
                    ensure_ascii=False,
                ),
                "evidence_ids_json": json.dumps(list(t.evidence_ids)),
                "member_count": members.get(t.topic_id, 0),
            }
            for t in stored.topics
        ],
        "sentiment": [
            {
                "dataset_id": dataset_id,
                "record_id": s.record_id,
                "status": s.status,
                "model_label": s.model_label,
                **{
                    label: (s.probabilities or {}).get(label)
                    for label in ("negative", "neutral", "positive")
                },
                "reason": s.reason,
                "input_sha256": s.input_sha256,
            }
            for s in stored.sentiments
        ],
        "interpretations": [
            {
                "dataset_id": dataset_id,
                "topic_id": topic_id,
                "status": record["status"],
                "name": (record["result"] or {}).get("name"),
                "topic_description": (record["result"] or {}).get("topic_description"),
                "proposed_feature": (record["result"] or {}).get("proposed_feature"),
                "assessment": (record["result"] or {}).get("assessment"),
                "supporting_review_ids_json": None
                if record["result"] is None
                else json.dumps(record["result"]["supporting_review_ids"]),
                "requested_model": record["provider"]["requested_model"],
                "prompt_version": record["prompt_version"],
                "attempt_id": record["attempt_id"],
                "created_at": record["created_at"],
            }
            for topic_id, record in sorted(interpreted.items())
        ],
    }
    validate_description(description)
    return description
