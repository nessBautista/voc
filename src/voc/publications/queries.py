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


# These are capabilities, not an interface for executing arbitrary SQL.
_OPERATIONS = {
    "overview": _overview,
    "sources": _sources,
    "coverage": _coverage,
    "freshness": _freshness,
    "embeddings": _embeddings,
}


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
        raise ValueError(
            "Capability not available; use overview, sources, coverage, freshness or embeddings"
        )
    with open_publication(
        catalog,
        publication_id,
    ) as opened:
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
