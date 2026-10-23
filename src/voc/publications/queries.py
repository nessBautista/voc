"""Named, reproducible data-state queries shared by CLI and future applications."""

import hashlib
import json
from datetime import UTC, datetime

from .contracts import QUERY_SCHEMA_VERSION
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
KINDS = tuple(_OPERATIONS)


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


def query(catalog, publication_id, kind):
    """Read an explicit publication through a supported operation and cite its result.

    Stable result IDs include publication, query schema, operation, data and all
    publication warnings. They exclude read/activation times and filesystem paths.
    """
    if (
        not isinstance(
            kind,
            str,
        )
        or kind not in _OPERATIONS
    ):
        raise CapabilityUnavailable(
            "Capability not available; use one of: " + ", ".join(KINDS)
        )
    with open_publication(
        catalog,
        publication_id,
    ) as opened:
        if kind not in opened.manifest["capabilities"]:
            raise CapabilityUnavailable(
                f"Capability not available in this publication: {kind}"
            )
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
