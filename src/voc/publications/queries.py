"""Named, reproducible data-state queries shared by CLI and future applications."""

import hashlib
import json
from datetime import UTC, datetime

from voc.analysis.highlight import wilson_lower

from .contracts import QUERY_SCHEMA_VERSION, QUERY_SCHEMA_VERSION_V2
from .selection import (
    ANALYSIS_KINDS,
    SelectionError,
    canonical_selection,
    like_pattern,
)
from .store import open_publication


def _rows(connection, sql, dataset_id):
    # SQL comes only from the functions below; dataset identity is a bound value.
    result = connection.execute(
        sql,
        [dataset_id],
    )
    columns = [column[0] for column in result.description]
    return [
        dict(
            zip(
                columns,
                row,
                strict=True,
            )
        )
        for row in result.fetchall()
    ]


def _overview(connection, dataset_id):
    return _rows(
        connection,
        "SELECT dataset_id, source_kind, source_version, row_count, unknown_dates, "
        "record_start, record_end FROM dataset WHERE dataset_id = ?",
        dataset_id,
    )[0]


def _sources(connection, dataset_id):
    return _rows(
        connection,
        "SELECT platform, record_count, unknown_dates, collection_state, "
        "latest_collection_at, collection_evidence_id "
        "FROM sources WHERE dataset_id = ? ORDER BY platform",
        dataset_id,
    )


def _coverage(connection, dataset_id):
    return {
        "months": _rows(
            connection,
            "SELECT month, record_count FROM monthly_counts "
            "WHERE dataset_id = ? ORDER BY month",
            dataset_id,
        ),
        "unknown_dates": _rows(
            connection,
            "SELECT unknown_dates FROM dataset WHERE dataset_id = ?",
            dataset_id,
        )[0]["unknown_dates"],
    }


def _freshness(connection, dataset_id):
    # Preparation, publication and collection dates describe different events.
    dates = _rows(
        connection,
        "SELECT d.dataset_id, d.prepared_at, p.created_at AS publication_created_at "
        "FROM dataset d JOIN publication p USING (dataset_id) WHERE d.dataset_id = ?",
        dataset_id,
    )[0]
    dates["sources"] = _rows(
        connection,
        "SELECT platform, collection_state, latest_collection_at, collection_evidence_id "
        "FROM sources WHERE dataset_id = ? ORDER BY platform",
        dataset_id,
    )
    dates["evidence"] = _rows(
        connection,
        "SELECT evidence_id, kind, evidence_ref, description FROM provenance "
        "WHERE dataset_id = ? AND evidence_id IN "
        "(SELECT collection_evidence_id FROM sources) ORDER BY evidence_id",
        dataset_id,
    )
    return dates


def _analysis(connection, dataset_id):
    row = _rows(
        connection,
        "SELECT analysis_id, parent_analysis_id, embedding_release_id, embedding_profile, "
        "selection_kind, selected_reviews, assigned_reviews, unassigned_reviews, topic_count, "
        "classified, excluded, failed, configuration_json, code_sha256, manifest_sha256, created_at "
        "FROM analysis WHERE dataset_id = ?",
        dataset_id,
    )[0]
    row["configuration"] = json.loads(row.pop("configuration_json"))
    return row


def _topics(connection, dataset_id):
    rows = _rows(
        connection,
        "SELECT t.topic_id, t.name, t.description, t.keywords_json, t.evidence_ids_json, "
        "t.member_count, "
        "count(*) FILTER (WHERE s.status = 'classified') AS classified, "
        "count(*) FILTER (WHERE s.model_label = 'negative') AS negative, "
        "count(*) FILTER (WHERE s.model_label = 'neutral') AS neutral, "
        "count(*) FILTER (WHERE s.model_label = 'positive') AS positive, "
        "i.status AS interpretation_status, i.name AS interpreted_name, "
        "i.topic_description, i.proposed_feature, i.assessment, i.supporting_review_ids_json "
        "FROM topics t "
        "LEFT JOIN assignments a ON a.dataset_id = t.dataset_id AND a.topic_id = t.topic_id "
        "LEFT JOIN sentiment s ON s.dataset_id = a.dataset_id AND s.record_id = a.record_id "
        "LEFT JOIN interpretations i ON i.dataset_id = t.dataset_id AND i.topic_id = t.topic_id "
        "WHERE t.dataset_id = ? "
        "GROUP BY ALL ORDER BY t.member_count DESC, t.topic_id",
        dataset_id,
    )
    for row in rows:
        row["keywords"] = json.loads(row.pop("keywords_json"))
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        supporting = row.pop("supporting_review_ids_json")
        row["supporting_review_ids"] = (
            None if supporting is None else json.loads(supporting)
        )
        generated = row["interpretation_status"] == "completed"
        row["display_name"] = row["interpreted_name"] if generated else row["name"]
        row["wording_source"] = "generated" if generated else "original"
    return rows


def _sentiment(connection, dataset_id):
    totals = _rows(
        connection,
        "SELECT count(*) AS selected_reviews, "
        "count(*) FILTER (WHERE status = 'classified') AS classified, "
        "count(*) FILTER (WHERE status = 'excluded') AS excluded, "
        "count(*) FILTER (WHERE status = 'failed') AS failed, "
        "count(*) FILTER (WHERE model_label = 'negative') AS negative, "
        "count(*) FILTER (WHERE model_label = 'neutral') AS neutral, "
        "count(*) FILTER (WHERE model_label = 'positive') AS positive "
        "FROM sentiment WHERE dataset_id = ?",
        dataset_id,
    )[0]
    totals["by_platform"] = _rows(
        connection,
        "SELECT r.platform, count(*) AS selected_reviews, "
        "count(*) FILTER (WHERE s.status = 'classified') AS classified, "
        "count(*) FILTER (WHERE s.model_label = 'negative') AS negative, "
        "count(*) FILTER (WHERE s.model_label = 'neutral') AS neutral, "
        "count(*) FILTER (WHERE s.model_label = 'positive') AS positive "
        "FROM sentiment s JOIN reviews r ON r.dataset_id = s.dataset_id AND r.record_id = s.record_id "
        "WHERE s.dataset_id = ? GROUP BY r.platform ORDER BY r.platform",
        dataset_id,
    )
    return totals


def _embeddings(connection, dataset_id):
    result = _rows(
        connection,
        "SELECT dataset_id, state, release_id, model, covered_rows, "
        "verification_evidence_id FROM embedding_status WHERE dataset_id = ?",
        dataset_id,
    )[0]
    result["evidence"] = _rows(
        connection,
        "SELECT evidence_id, kind, evidence_ref, description FROM provenance "
        "WHERE dataset_id = ? AND kind IN ('embedding_registration', 'embedding_verification') "
        "AND evidence_ref = (SELECT 'embedding-release:' || release_id FROM embedding_status) "
        "ORDER BY evidence_id",
        dataset_id,
    )
    return result


# ---- Analysis kinds: one selection, six views of a version 2 package ----------
#
# Every analysis kind reads the same "in_view" rows: one per review, joined to
# its topic, its sentiment and its topic's interpretation, filtered by the
# selection with bound parameters only. Shares use classified reviews as the
# denominator; unclassified reviews count in totals and are never labelled.

_VIEW_BASE = (
    "WITH base AS ("
    "SELECT r.record_id, r.platform, substr(r.record_date, 1, 7) AS month, r.record_date, "
    "r.rating, r.model_text, a.topic_id, s.status, s.model_label, "
    "s.negative, s.neutral, s.positive, "
    "CASE WHEN a.topic_id = -1 THEN 'unassigned' "
    "WHEN i.status = 'completed' THEN i.assessment ELSE 'uninterpreted' END AS assessment "
    "FROM reviews r "
    "JOIN assignments a ON a.dataset_id = r.dataset_id AND a.record_id = r.record_id "
    "JOIN sentiment s ON s.dataset_id = r.dataset_id AND s.record_id = r.record_id "
    "LEFT JOIN interpretations i ON i.dataset_id = r.dataset_id AND i.topic_id = a.topic_id "
    "WHERE r.dataset_id = ?"
    "), in_view AS (SELECT * FROM base WHERE 1 = 1{filters}) "
)


def _counts(alias=""):
    """The five counts every analysis view reports, optionally table-qualified."""
    prefix = f"{alias}." if alias else ""
    return (
        "count(*) AS total, "
        f"count(*) FILTER (WHERE {prefix}status = 'classified') AS classified, "
        f"count(*) FILTER (WHERE {prefix}model_label = 'negative') AS negative, "
        f"count(*) FILTER (WHERE {prefix}model_label = 'neutral') AS neutral, "
        f"count(*) FILTER (WHERE {prefix}model_label = 'positive') AS positive"
    )


_COUNTS = _counts()


def _view(dataset_id, selection, *, topic_id=None):
    """The CTE prefix and its bound parameters for one canonical selection."""
    filters, params = [], [dataset_id]
    for column, values in (
        ("platform", selection["platforms"]),
        ("model_label", selection["labels"]),
        ("assessment", selection["assessments"]),
    ):
        if values:
            filters.append(f" AND {column} IN ({', '.join('?' * len(values))})")
            params.extend(values)
    if selection["month_from"]:
        filters.append(" AND month >= ?")
        params.append(selection["month_from"])
    if selection["month_to"]:
        filters.append(" AND month <= ?")
        params.append(selection["month_to"])
    if selection["search"]:
        filters.append(" AND model_text ILIKE ? ESCAPE '\\'")
        params.append(like_pattern(selection["search"]))
    if topic_id is not None:
        filters.append(" AND topic_id = ?")
        params.append(topic_id)
    return _VIEW_BASE.format(filters="".join(filters)), params


def _run(connection, sql, params):
    result = connection.execute(sql, params)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def _share(part, whole):
    return None if not whole else part / whole


def _topic_rows(connection, dataset_id, selection, *, topic_id=None):
    """Per-topic counts under the selection, joined to names and interpretations."""
    prefix, params = _view(dataset_id, selection, topic_id=topic_id)
    rows = _run(
        connection,
        prefix
        + "SELECT v.topic_id, "
        + _counts("v")
        + ", t.name, t.description, t.keywords_json, t.evidence_ids_json, t.member_count, "
        "i.status AS interpretation_status, i.name AS interpreted_name, i.topic_description, "
        "i.proposed_feature, i.assessment AS interpreted_assessment, i.supporting_review_ids_json "
        "FROM in_view v "
        "JOIN topics t ON t.dataset_id = ? AND t.topic_id = v.topic_id "
        "LEFT JOIN interpretations i ON i.dataset_id = t.dataset_id AND i.topic_id = t.topic_id "
        "GROUP BY ALL ORDER BY v.topic_id",
        [*params, dataset_id],
    )
    for row in rows:
        generated = row["interpretation_status"] == "completed"
        row["display_name"] = row.pop("interpreted_name") if generated else row["name"]
        row["wording_source"] = "generated" if generated else "original"
        row["assessment"] = (
            "unassigned"
            if row["topic_id"] == -1
            else (row.pop("interpreted_assessment") if generated else "uninterpreted")
        )
        row.pop("interpreted_assessment", None)
        row["keywords"] = [k["term"] for k in json.loads(row.pop("keywords_json"))][:6]
        row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        supporting = row.pop("supporting_review_ids_json")
        row["supporting_review_ids"] = (
            None if supporting is None else json.loads(supporting)
        )
        row["negative_share"] = _share(row["negative"], row["classified"])
        row["positive_share"] = _share(row["positive"], row["classified"])
        row["wilson_lower"] = (
            None
            if not row["classified"]
            else wilson_lower(row["negative"], row["classified"])
        )
    return rows


def _feature_topics(rows, minimum=5):
    """Feature topics with enough labels, ranked by the Wilson lower bound."""
    eligible = [
        r
        for r in rows
        if r["topic_id"] >= 0
        and r["assessment"] in ("single_feature", "mixed")
        and r["classified"] >= minimum
    ]
    eligible.sort(key=lambda r: (-r["wilson_lower"], -r["classified"], r["topic_id"]))
    return eligible


def _summary(connection, dataset_id, selection):
    prefix, params = _view(dataset_id, selection)
    counts = _run(
        connection,
        prefix
        + "SELECT "
        + _COUNTS
        + ", count(DISTINCT topic_id) FILTER (WHERE topic_id >= 0) AS topics_in_view "
        "FROM in_view",
        params,
    )[0]
    totals = _run(
        connection,
        "SELECT selected_reviews, topic_count FROM analysis WHERE dataset_id = ?",
        [dataset_id],
    )[0]
    rows = _topic_rows(connection, dataset_id, selection)
    feature = _feature_topics(rows)
    top = feature[0] if feature else None
    classified = counts["classified"]
    return {
        "reviews_in_view": counts["total"],
        "reviews_total": totals["selected_reviews"],
        "classified": classified,
        "topics_in_view": counts["topics_in_view"],
        "topics_total": totals["topic_count"],
        "negative": {
            "count": counts["negative"],
            "share": _share(counts["negative"], classified),
        },
        "neutral": {
            "count": counts["neutral"],
            "share": _share(counts["neutral"], classified),
        },
        "positive": {
            "count": counts["positive"],
            "share": _share(counts["positive"], classified),
        },
        "most_negative_feature": None
        if top is None
        else {
            "topic_id": top["topic_id"],
            "display_name": top["display_name"],
            "assessment": top["assessment"],
            "negative": top["negative"],
            "total": top["classified"],
            "share": top["negative_share"],
            "wilson_lower": top["wilson_lower"],
            "eligible_topics": len(feature),
        },
    }


def _topic_list(connection, dataset_id, selection):
    rows = _topic_rows(connection, dataset_id, selection)
    sort = selection["sort"]
    keys = {
        "volume": lambda r: (-r["total"], r["topic_id"]),
        "negative": lambda r: (-(r["negative_share"] or 0), -r["total"], r["topic_id"]),
        "positive": lambda r: (-(r["positive_share"] or 0), -r["total"], r["topic_id"]),
    }
    assigned = sorted((r for r in rows if r["topic_id"] >= 0), key=keys[sort])
    unassigned = [r for r in rows if r["topic_id"] == -1]
    fields = (
        "topic_id",
        "display_name",
        "wording_source",
        "assessment",
        "keywords",
        "member_count",
        "total",
        "classified",
        "negative",
        "neutral",
        "positive",
        "negative_share",
        "positive_share",
        "wilson_lower",
    )
    return [{k: r[k] for k in fields} for r in (*assigned, *unassigned)]


def _month_range(connection, dataset_id, selection):
    bounds = _run(
        connection,
        "SELECT min(substr(record_date, 1, 7)) AS first, max(substr(record_date, 1, 7)) AS last "
        "FROM reviews WHERE dataset_id = ? AND record_date IS NOT NULL",
        [dataset_id],
    )[0]
    first = max(filter(None, (bounds["first"], selection["month_from"])), default=None)
    last = min(filter(None, (bounds["last"], selection["month_to"])), default=None)
    months = []
    if first and last and first <= last:
        year, month = int(first[:4]), int(first[5:])
        while f"{year:04d}-{month:02d}" <= last:
            months.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _timeline(connection, dataset_id, selection):
    topic_id = selection["topic_id"]
    prefix, params = _view(dataset_id, selection, topic_id=topic_id)
    counted = {
        row["month"]: row
        for row in _run(
            connection,
            prefix
            + "SELECT month, "
            + _COUNTS
            + " FROM in_view WHERE month IS NOT NULL GROUP BY month",
            params,
        )
    }
    months = [
        {
            "month": month,
            **{
                k: counted.get(month, {}).get(k, 0)
                for k in ("total", "classified", "negative", "neutral", "positive")
            },
        }
        for month in _month_range(connection, dataset_id, selection)
    ]
    busiest = max(months, key=lambda m: (m["total"], -months.index(m)), default=None)
    return {
        "topic_id": topic_id,
        "months": months,
        "busiest": None
        if busiest is None or busiest["total"] == 0
        else {"month": busiest["month"], "total": busiest["total"]},
        "reviews_in_view": sum(m["total"] for m in months),
    }


def _platforms(connection, dataset_id, selection):
    prefix, params = _view(dataset_id, selection)
    rows = _run(
        connection,
        prefix + "SELECT platform, " + _COUNTS + " FROM in_view GROUP BY platform "
        "ORDER BY total DESC, platform",
        params,
    )
    for row in rows:
        row["negative_share"] = _share(row["negative"], row["classified"])
        row["positive_share"] = _share(row["positive"], row["classified"])
    return rows


def _topic_detail(connection, dataset_id, selection):
    topic_id = selection["topic_id"]
    rows = _topic_rows(connection, dataset_id, selection, topic_id=topic_id)
    if not rows:
        # The topic exists in the package but has no review under this selection.
        known = _run(
            connection,
            "SELECT t.topic_id, t.name, t.description, t.keywords_json, t.evidence_ids_json, "
            "t.member_count, i.status AS interpretation_status, i.name AS interpreted_name, "
            "i.topic_description, i.proposed_feature, i.assessment, i.supporting_review_ids_json "
            "FROM topics t LEFT JOIN interpretations i ON i.dataset_id = t.dataset_id AND i.topic_id = t.topic_id "
            "WHERE t.dataset_id = ? AND t.topic_id = ?",
            [dataset_id, topic_id],
        )
        if not known:
            raise LookupError(f"Topic {topic_id} is not in this publication")
        base = known[0]
        generated = base["interpretation_status"] == "completed"
        return {
            "topic_id": topic_id,
            "display_name": base["interpreted_name"] if generated else base["name"],
            "wording_source": "generated" if generated else "original",
            "assessment": "unassigned"
            if topic_id == -1
            else (base["assessment"] if generated else "uninterpreted"),
            "description": base["topic_description"]
            if generated
            else base["description"],
            "proposed_feature": base["proposed_feature"],
            "interpretation_status": base["interpretation_status"],
            "keywords": [k["term"] for k in json.loads(base["keywords_json"])][:6],
            "evidence_ids": json.loads(base["evidence_ids_json"]),
            "supporting_review_ids": None
            if base["supporting_review_ids_json"] is None
            else json.loads(base["supporting_review_ids_json"]),
            "member_count": base["member_count"],
            "in_view": 0,
            "classified": 0,
            "negative": 0,
            "neutral": 0,
            "positive": 0,
            "negative_share": None,
            "positive_share": None,
            "wilson_lower": None,
        }
    row = rows[0]
    generated = row["wording_source"] == "generated"
    return {
        "topic_id": topic_id,
        "display_name": row["display_name"],
        "wording_source": row["wording_source"],
        "assessment": row["assessment"],
        "description": row["topic_description"] if generated else row["description"],
        "proposed_feature": row["proposed_feature"],
        "interpretation_status": row["interpretation_status"],
        "keywords": row["keywords"],
        "evidence_ids": row["evidence_ids"],
        "supporting_review_ids": row["supporting_review_ids"],
        "member_count": row["member_count"],
        "in_view": row["total"],
        "classified": row["classified"],
        "negative": row["negative"],
        "neutral": row["neutral"],
        "positive": row["positive"],
        "negative_share": row["negative_share"],
        "positive_share": row["positive_share"],
        "wilson_lower": row["wilson_lower"],
    }


def _reviews(connection, dataset_id, selection):
    prefix, params = _view(dataset_id, selection, topic_id=selection["topic_id"])
    total = _run(connection, prefix + "SELECT count(*) AS total FROM in_view", params)[
        0
    ]["total"]
    page, size = selection["page"], selection["page_size"]
    items = _run(
        connection,
        prefix
        + "SELECT record_id, platform, record_date, month, rating, topic_id, status, "
        "model_label AS label, negative, neutral, positive, model_text AS text, "
        "greatest(negative, neutral, positive) AS confidence FROM in_view "
        "ORDER BY CASE model_label WHEN 'negative' THEN 0 WHEN 'neutral' THEN 1 "
        "WHEN 'positive' THEN 2 ELSE 3 END, confidence DESC NULLS LAST, record_id "
        "LIMIT ? OFFSET ?",
        [*params, size, (page - 1) * size],
    )
    return {
        "topic_id": selection["topic_id"],
        "total": total,
        "page": page,
        "page_size": size,
        "pages": (total + size - 1) // size,
        "items": items,
    }


_ANALYSIS_OPERATIONS = {
    "summary": _summary,
    "topic_list": _topic_list,
    "timeline": _timeline,
    "platforms": _platforms,
    "topic_detail": _topic_detail,
    "reviews": _reviews,
}
# The manifest capability each analysis kind needs; the manifest itself is unchanged.
_REQUIRES = {
    "summary": "sentiment",
    "topic_list": "topics",
    "timeline": "sentiment",
    "platforms": "sentiment",
    "topic_detail": "topics",
    "reviews": "reviews",
}
assert tuple(_ANALYSIS_OPERATIONS) == ANALYSIS_KINDS


class CapabilityUnavailable(ValueError):
    """The kind is unknown, or this publication's manifest does not list it."""


# These are capabilities, not an interface for executing arbitrary SQL. The
# last three exist only in v2 packages; reviews are stored but not queried here.
_OPERATIONS = {
    "overview": _overview,
    "sources": _sources,
    "coverage": _coverage,
    "freshness": _freshness,
    "embeddings": _embeddings,
    "analysis": _analysis,
    "topics": _topics,
    "sentiment": _sentiment,
}
KINDS = tuple(_OPERATIONS) + ANALYSIS_KINDS


def _json_value(value):
    # Normalize persisted timestamps before hashing or emitting a query result.
    if isinstance(
        value,
        datetime,
    ):
        return (
            value.astimezone(UTC)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )
    if isinstance(
        value,
        dict,
    ):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(
        value,
        list,
    ):
        return [_json_value(item) for item in value]
    return value


def query(catalog, publication_id, kind, selection=None):
    """Read an explicit publication through a supported operation and cite its result.

    Stable result IDs include publication, query schema, operation, data and all
    publication warnings; for analysis kinds they include the canonical
    selection too. They exclude read/activation times and filesystem paths.
    """
    if not isinstance(kind, str) or kind not in KINDS:
        raise CapabilityUnavailable(
            "Capability not available; use one of: " + ", ".join(KINDS)
        )
    analysis = kind in _ANALYSIS_OPERATIONS
    if analysis:
        canonical = canonical_selection(selection, kind)
    elif selection:
        raise SelectionError(f"{kind} takes no selection")
    with open_publication(
        catalog,
        publication_id,
    ) as opened:
        if _REQUIRES.get(kind, kind) not in opened.manifest["capabilities"]:
            raise CapabilityUnavailable(
                f"Capability not available in this publication: {kind}"
            )
        if analysis:
            result = _json_value(
                {
                    "schema_version": QUERY_SCHEMA_VERSION_V2,
                    "publication_id": opened.manifest["publication_id"],
                    "kind": kind,
                    "selection": canonical,
                    "data": _ANALYSIS_OPERATIONS[kind](
                        opened.connection, opened.manifest["dataset_id"], canonical
                    ),
                    "warnings": opened.warnings,
                }
            )
        else:
            result = _json_value(
                {
                    "schema_version": QUERY_SCHEMA_VERSION,
                    "publication_id": opened.manifest["publication_id"],
                    "kind": kind,
                    "data": _OPERATIONS[kind](
                        opened.connection,
                        opened.manifest["dataset_id"],
                    ),
                    "warnings": opened.warnings,
                }
            )
    # Hash a canonical representation so repeated reads provide the same reference.
    encoded = json.dumps(
        result,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {**result, "result_id": hashlib.sha256(encoded).hexdigest()}
