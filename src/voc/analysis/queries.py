"""Shared read-only queries for operators, agents and dashboard APIs.

Filters combine with AND. Categories use exact values. Text search uses a literal,
case-insensitive, accent-sensitive substring. Dates are inclusive UTC calendar
bounds (YYYY-MM-DD). Invalid/missing review dates stay in the null monthly bucket
and are excluded by date bounds. Sentiment proportions use non-null model labels
within the filtered population, including topic -1. Undefined proportions are
None. Topic IDs are local to a single analysis.

The ``sentiment_corrected``, ``sentiment_correction`` and ``topic_corrections``
fields are kept in every row for consumers; they are always false, None and
empty, because human corrections are not part of this release.
"""

import json
from datetime import date

from voc.analysis.contracts import ReviewAssignment
from voc.analysis.highlight import highlighted_topic
from voc.analysis.interpretations import (
    read_records,
    validate_records,
    verify_artifacts,
)
from voc.analysis.schema import (
    ASSESSMENTS,
    EVIDENCE,
    FILTERS,
    KINDS,
    PAGED,
    REVIEW_SORTS,
    SCHEMA_VERSION,
    SENTIMENT_LABELS,
    SENTIMENT_STATUSES,
    envelope,
)
from voc.analysis.store import (
    analysis_directory,
    list_analyses,
    open_analysis,
    read_manifest,
)

__all__ = [
    "ASSESSMENTS",
    "EVIDENCE",
    "FILTERS",
    "KINDS",
    "PAGED",
    "REVIEW_SORTS",
    "SCHEMA_VERSION",
    "SENTIMENT_LABELS",
    "SENTIMENT_STATUSES",
    "envelope",
    "inspect_analysis",
    "list_candidates",
    "query",
    "validate_query",
]


def list_candidates():
    return envelope(
        "list",
        [
            {
                "analysis_id": m.analysis_id,
                "created_at": m.created_at,
                "coverage": m.coverage,
                "parent_analysis_id": m.parent_analysis_id,
                "configuration": m.configuration,
            }
            for m in list_analyses()
        ],
    )


def inspect_analysis(analysis_id):
    manifest = read_manifest(analysis_id)
    return envelope("inspect", manifest.as_dict(), analysis_id=manifest.analysis_id)


def validate_query(kind, filters=None, limit=50, offset=0):
    """Validate request values before opening storage; return normalized filters."""
    if kind not in KINDS:
        raise ValueError(f"Unknown query kind. Choose from: {', '.join(KINDS)}")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000")
    if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
        raise ValueError("offset must be a nonnegative 64-bit integer")
    if filters is not None and not isinstance(filters, dict):
        raise ValueError("filters must be a mapping")
    filters = {} if filters is None else dict(filters)
    if set(filters) - set(FILTERS):
        raise ValueError("Unknown filter names")
    f = {key: filters.get(key) for key in FILTERS}
    if kind in ("metadata", "diagnostics") and any(v is not None for v in f.values()):
        raise ValueError(
            "Metadata and diagnostics describe the entire analysis and accept no filters"
        )
    if f["platform"] is not None and (
        not isinstance(f["platform"], str) or not f["platform"].strip()
    ):
        raise ValueError("platform must be a nonblank string")
    if f["search"] is not None:
        if (
            not isinstance(f["search"], str)
            or not f["search"].strip()
            or len(f["search"]) > 500
        ):
            raise ValueError("search must contain 1–500 characters")
        f["search"] = f["search"].strip()
    for key, choices in (
        ("assessment", ASSESSMENTS),
        ("evidence", EVIDENCE),
        ("sentiment", SENTIMENT_LABELS),
        ("status", SENTIMENT_STATUSES),
    ):
        if f[key] is not None and f[key] not in choices:
            raise ValueError(f"Unknown {key}")
    if f["topic_id"] is not None and (
        type(f["topic_id"]) is not int or not -1 <= f["topic_id"] <= 2**31 - 1
    ):
        raise ValueError("topic_id must be an integer from -1 to 2147483647")
    for key in ("start", "end"):
        value = f[key]
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"{key} must use YYYY-MM-DD")
            try:
                parsed = date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"{key} must use YYYY-MM-DD") from error
            if parsed.isoformat() != value:
                raise ValueError(f"{key} must use YYYY-MM-DD")
    if f["start"] and f["end"] and f["start"] > f["end"]:
        raise ValueError("start must be on or before end")
    return f


# Names/expressions are internal constants. All user values are bound parameters.
_BASE = """WITH joined AS (
    SELECT r.record_id, r.model_text, r.platform, {rating} AS rating,
           CAST(r.record_date AS VARCHAR) AS record_date, r.vector_row,
           CAST(try_cast(r.record_date AS TIMESTAMPTZ) AT TIME ZONE 'UTC' AS DATE) AS review_date,
           a.topic_id, a.model_topic_id, a.membership_strength,
           s.status, s.model_label, s.label AS sentiment, {corrected} AS sentiment_corrected,
           s.negative, s.neutral, s.positive, s.reason, s.input_sha256,
           CASE WHEN s.status = 'classified' THEN greatest(s.negative, s.neutral, s.positive) END AS model_confidence,
           CASE WHEN s.status = 'classified' AND NOT ({corrected}) THEN greatest(s.negative, s.neutral, s.positive) END AS sorting_confidence
    FROM reviews r JOIN effective_assignments a USING(record_id)
    JOIN effective_sentiment s USING(record_id)
), selected AS (SELECT * FROM joined {where}) """
_COUNTS = """count(*) AS selected_reviews,
    count(*) FILTER (WHERE topic_id != -1) AS assigned_reviews,
    count(*) FILTER (WHERE topic_id = -1) AS unassigned_reviews,
    count(DISTINCT topic_id) FILTER (WHERE topic_id >= 0) AS topic_count,
    count(*) FILTER (WHERE status = 'classified') AS classified,
    count(*) FILTER (WHERE status = 'excluded') AS excluded,
    count(*) FILTER (WHERE status = 'failed') AS failed,
    count(*) FILTER (WHERE sentiment = 'negative') AS negative,
    count(*) FILTER (WHERE sentiment = 'neutral') AS neutral,
    count(*) FILTER (WHERE sentiment = 'positive') AS positive,
    count(*) FILTER (WHERE sentiment_corrected) AS corrected_reviews,
    count(*) FILTER (WHERE sentiment_corrected AND status != 'classified') AS corrected_unclassified_reviews,
    count(*) FILTER (WHERE model_label = 'negative') AS model_negative,
    count(*) FILTER (WHERE model_label = 'neutral') AS model_neutral,
    count(*) FILTER (WHERE model_label = 'positive') AS model_positive"""


def _selection(
    filters, corrected=False, *, rating="NULL", assessed_ids=None, evidence_ids=None
):
    clauses, values = [], []
    for key in ("platform", "sentiment", "status", "topic_id"):
        if filters[key] is not None:
            clauses.append(f"{key} = ?")
            values.append(filters[key])
    for key, op in (("start", ">="), ("end", "<=")):
        if filters[key] is not None:
            clauses.append(f"review_date {op} CAST(? AS DATE)")
            values.append(filters[key])
    if filters["search"] is not None:
        clauses.append("contains(lower(model_text), lower(?))")
        values.append(filters["search"])
    if assessed_ids is not None:
        clauses.append("topic_id IN (SELECT unnest(?::INTEGER[]))")
        values.append(assessed_ids)
    if evidence_ids is not None:
        clauses.append("record_id IN (SELECT unnest(?::VARCHAR[]))")
        values.append(evidence_ids)
    return _BASE.format(
        rating=rating,
        where="WHERE " + " AND ".join(clauses) if clauses else "",
        corrected="s.sentiment_corrected" if corrected else "FALSE",
    ), values


def _rows(con, sql, values):
    cursor = con.execute(sql, values)
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _proportions(row):
    n = sum(row[label] for label in SENTIMENT_LABELS)
    row["model_sentiment_denominator"] = row["classified"]
    row["model_sentiment_counts"] = {
        label: row.pop("model_" + label) for label in SENTIMENT_LABELS
    }
    row["model_sentiment_proportions"] = {
        label: count / row["classified"] if row["classified"] else None
        for label, count in row["model_sentiment_counts"].items()
    }
    row["sentiment_denominator"] = n
    row["sentiment_proportions"] = {
        label: row[label] / n if n else None for label in SENTIMENT_LABELS
    }
    return row


def _diagnostics(manifest):
    """Return a bounded summary from checksum-verified saved workflow evidence."""
    interpretation = manifest.artifacts.get("artifacts/interpretation-evidence.json")
    if interpretation is not None:
        path = analysis_directory(manifest.analysis_id) / interpretation["path"]
        saved = json.loads(path.read_bytes())
        if (
            saved.get("schema_version") != "voc-interpretation-evidence-v1"
            or saved.get("run_id") != manifest.provenance["run_id"]
            or saved.get("analysis_id") != manifest.analysis_id
            or saved.get("parent_analysis_id") != manifest.parent_analysis_id
            or saved.get("configuration") != manifest.configuration
            or saved.get("coverage") != manifest.coverage
        ):
            raise ValueError(
                "Saved interpretation diagnostics differ from analysis provenance"
            )
        return saved, True, None
    representation = manifest.artifacts.get("artifacts/representation-evidence.json")
    if representation is not None:
        path = analysis_directory(manifest.analysis_id) / representation["path"]
        saved = json.loads(path.read_bytes())
        if (
            saved.get("schema_version") != "voc-representation-evidence-v1"
            or saved.get("run_id") != manifest.provenance["run_id"]
            or saved.get("analysis_id") != manifest.analysis_id
            or saved.get("parent_analysis_id") != manifest.parent_analysis_id
            or saved.get("parent_manifest_sha256")
            != manifest.provenance.get("parent_manifest_sha256")
            or saved.get("configuration") != manifest.configuration
            or saved.get("coverage") != manifest.coverage
        ):
            raise ValueError(
                "Saved representation diagnostics differ from analysis provenance"
            )
        return saved, True, None
    artifact = manifest.artifacts.get("artifacts/workflow-evidence.json")
    if artifact is None:
        return {}, False, "No saved workflow diagnostics"
    path = analysis_directory(manifest.analysis_id) / artifact["path"]
    saved = json.loads(path.read_bytes())
    if (
        saved.get("schema_version") != "voc-workflow-evidence-v1"
        or saved.get("run_id") != manifest.provenance["run_id"]
        or saved.get("configuration") != manifest.configuration
        or saved.get("coverage") != manifest.coverage
    ):
        raise ValueError("Saved diagnostics differ from analysis provenance")
    sentiment = saved.get("sentiment", {})
    topics = saved.get("topics") or {}
    return (
        {
            "coverage": saved["coverage"],
            "sentiment_counts": sentiment.get("counts"),
            "sentiment_configuration": sentiment.get("configuration"),
            "reduction_reused": topics.get("reduction_reused"),
            "representation": topics.get("representation"),
            "representative_policy": topics.get("representative_policy"),
            "measurement": saved.get("measurement"),
        },
        True,
        None,
    )


def selection_context(con, manifest, filters):
    """Verified selection shared by read-only query and exploration operations."""
    # Interpretations are verified before they drive eligibility or evidence filters.
    interpretations = read_records(con, manifest)
    validate_records(
        interpretations,
        [
            ReviewAssignment(*a)
            for a in con.execute(
                "SELECT record_id, topic_id, membership_strength FROM assignments"
            ).fetchall()
        ],
    )
    verify_artifacts(
        interpretations, manifest, analysis_directory(manifest.analysis_id)
    )
    interpreted = {r["topic_id"]: r for r in interpretations}
    representative_ids = {
        rid
        for (raw,) in con.execute("SELECT evidence_ids FROM topics").fetchall()
        for rid in json.loads(raw)
    }
    cited_ids = {
        rid
        for r in interpretations
        if r["status"] == "completed"
        for rid in r["result"]["supporting_review_ids"]
    }
    assessed_ids = None
    if filters["assessment"] is not None:
        assessed_ids = []
        for (tid,) in con.execute("SELECT topic_id FROM topics").fetchall():
            record = interpreted.get(tid)
            assessment = (
                (
                    record["result"]["assessment"]
                    if record["status"] == "completed"
                    else "failed"
                )
                if record
                else "not_run"
            )
            if assessment == filters["assessment"]:
                assessed_ids.append(tid)
    evidence_ids = None
    if filters["evidence"]:
        evidence_ids = sorted(
            representative_ids if filters["evidence"] == "representative" else cited_ids
        )
    columns = {row[0] for row in con.execute("DESCRIBE reviews").fetchall()}
    rating = (
        "CASE WHEN isfinite(try_cast(r.rating AS DOUBLE)) THEN try_cast(r.rating AS DOUBLE) END"
        if "rating" in columns
        else "NULL"
    )
    selection, values = _selection(
        filters,
        rating=rating,
        assessed_ids=assessed_ids,
        evidence_ids=evidence_ids,
    )
    return selection, values, interpreted, representative_ids, cited_ids


def query(
    analysis_id, kind="overview", *, filters=None, limit=50, offset=0, sort="source"
):
    """Query a checksum-verified candidate. Pagination applies to reviews.

    All responses include the pinned selection coverage and (except metadata/diagnostics)
    coverage of the filtered population. Lists of topics/buckets are unpaginated.
    Topic keywords/evidence describe the original topic, even with review filters.
    """
    filters = validate_query(kind, filters, limit, offset)
    if sort not in REVIEW_SORTS or (kind != "reviews" and sort != "source"):
        raise ValueError("Review sorting is only available for reviews")
    with open_analysis(analysis_id) as (con, manifest):
        result = envelope(kind, None, analysis_id=manifest.analysis_id)
        result.update(filters=filters, analysis_coverage=manifest.coverage)
        if kind == "diagnostics":
            data, available, reason = _diagnostics(manifest)
            result.update(data=data, available=available, reason=reason)
            return result
        if kind == "metadata":
            result["data"] = manifest.as_dict()
            return result
        selection, values, interpreted, representative_ids, cited_ids = (
            selection_context(con, manifest, filters)
        )
        coverage = _proportions(
            _rows(con, selection + f"SELECT {_COUNTS} FROM selected", values)[0]
        )
        result["coverage"] = coverage
        if kind == "overview":
            result["data"] = coverage
        elif kind in ("topics", "highlight"):
            result["data"] = _rows(
                con,
                selection
                + f"""
                SELECT t.*, c.* EXCLUDE(topic_id) FROM
                (SELECT topic_id, {_COUNTS} FROM selected GROUP BY topic_id) c
                JOIN topics t USING(topic_id) ORDER BY selected_reviews DESC, t.topic_id
            """,
                values,
            )
            for row in result["data"]:
                record = interpreted.get(row["topic_id"])
                row["interpretation"] = record
                row["interpretation_status"] = record["status"] if record else "not_run"
                generated = (
                    record["result"]
                    if record and record["status"] == "completed"
                    else None
                )
                row["display_name"] = generated["name"] if generated else row["name"]
                row["display_description"] = (
                    generated["topic_description"] if generated else row["description"]
                )
                row["wording_source"] = "generated" if generated else "original"
                row["topic_corrections"] = {}
                row["wording_sources"] = {
                    "name": row["wording_source"],
                    "description": row["wording_source"],
                }
                row["keywords"] = json.loads(row["keywords"])
                row["evidence_ids"] = json.loads(row["evidence_ids"])
                _proportions(row)
            if kind == "highlight":
                result["data"] = highlighted_topic(result["data"])
        elif kind in ("monthly", "platforms"):
            bucket = (
                "strftime(review_date, '%Y-%m')" if kind == "monthly" else "platform"
            )
            result["data"] = _rows(
                con,
                selection
                + f"SELECT {bucket} AS bucket, {_COUNTS} FROM selected GROUP BY 1 ORDER BY 1 NULLS LAST",
                values,
            )
            for row in result["data"]:
                _proportions(row)
        else:
            result.update(
                total=coverage["selected_reviews"], limit=limit, offset=offset
            )
            order = {
                "source": "vector_row, record_id",
                "sentiment": "CASE sentiment WHEN 'negative' THEN 0 WHEN 'neutral' THEN 1 WHEN 'positive' THEN 2 ELSE 3 END, sorting_confidence DESC NULLS LAST, vector_row, record_id",
                "model_confidence": "sorting_confidence DESC NULLS LAST, vector_row, record_id",
            }[sort]
            sql = f"SELECT * EXCLUDE(review_date, sorting_confidence) FROM selected ORDER BY {order} LIMIT ? OFFSET ?"
            result["sort"] = sort
            result["data"] = _rows(con, selection + sql, values + [limit, offset])
            for row in result["data"]:
                row["is_representative"] = row["record_id"] in representative_ids
                row["is_cited"] = row["record_id"] in cited_ids
                row["sentiment_correction"] = None
                row["sentiment_source"] = "model" if row["sentiment"] else None
        return result


def overview(analysis_id, **filters):
    return query(analysis_id, "overview", filters=filters)


def topics(analysis_id, **filters):
    return query(analysis_id, "topics", filters=filters)


def reviews(analysis_id, *, limit=50, offset=0, sort="source", **filters):
    return query(
        analysis_id, "reviews", filters=filters, limit=limit, offset=offset, sort=sort
    )


def monthly(analysis_id, **filters):
    return query(analysis_id, "monthly", filters=filters)


def platforms(analysis_id, **filters):
    return query(analysis_id, "platforms", filters=filters)


def metadata(analysis_id):
    return query(analysis_id, "metadata")
