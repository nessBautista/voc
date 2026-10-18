"""Logical contracts for published dataset state, independent of ML and web tools.

Descriptions contain normalized table rows, not raw reviews. Validation checks
consistency and evidence links; the exporter must verify real source evidence.
"""

import re
from datetime import UTC, date, datetime
from uuid import UUID

from .schema import TABLE_COLUMNS

SCHEMA_VERSION = "voc-data-state-v1"
QUERY_SCHEMA_VERSION = "voc-data-query-v1"

_DESCRIPTION_FIELDS = (
    "dataset",
    "provenance",
    "sources",
    "monthly_counts",
    "embedding_status",
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}")
_EVIDENCE_SCHEMES = (
    "fixture",
    "collection",
    "import",
    "dataset-release",
    "dataset-artifact",
    "embedding-release",
)


def _fields(value, expected, label):
    # Reject missing or extra columns before reading individual fields.
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label} must contain exactly: {', '.join(expected)}")


def _identifier(value, label):
    # Require a stable reference that can travel with a published dataset.
    if (
        not isinstance(value, str)
        or not _IDENTIFIER.fullmatch(value)
        or value.lower() == "latest"
    ):
        raise ValueError(
            f"{label} must be a fixed public identifier, not a path or latest"
        )


def _count(value, label, *, positive=False):
    # Match DuckDB BIGINT bounds; booleans must not count as integers.
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value < 2**63:
        raise ValueError(f"{label} must be an integer between {minimum} and 2**63 - 1")


def utc_datetime(value, label):
    """Read an explicit timezone-aware date and normalize its instant to UTC."""
    # Parse text without assuming a timezone for incomplete timestamps.
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{label} must be an ISO 8601 timestamp") from error
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must include a timezone")

    # Preserve the instant while expressing it in the shared UTC timezone.
    return value.astimezone(UTC)


def _month(value):
    # Check the written format before asking the calendar to validate the month.
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}",
        value,
    ):
        raise ValueError("month must use YYYY-MM")
    try:
        date.fromisoformat(value + "-01")
    except ValueError as error:
        raise ValueError("month must be a valid YYYY-MM") from error


def _rows(description, table, dataset_id, unique_key):
    # Validate row structure, dataset ownership and uniqueness together.
    rows = description[table]
    if not isinstance(rows, list):
        raise ValueError(f"{table} must be a list of rows")  # noqa: TRY004 - uniform contract errors
    seen = set()
    for row in rows:
        _fields(
            row,
            TABLE_COLUMNS[table],
            table,
        )
        if row["dataset_id"] != dataset_id:
            raise ValueError(f"{table} refers to another dataset")
        _identifier(
            row[unique_key],
            f"{table}.{unique_key}",
        )
        if row[unique_key] in seen:
            raise ValueError(f"{table} contains a duplicate {unique_key}")
        seen.add(row[unique_key])
    return rows


def _evidence_reference(value):
    # Publish logical references; URLs, filesystem paths and query tokens are not IDs.
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("evidence_ref must be a public logical reference")
    scheme, identifier = value.split(
        ":",
        1,
    )
    if scheme not in _EVIDENCE_SCHEMES:
        raise ValueError("evidence_ref uses an unsupported reference scheme")
    parts = identifier.split("/") if scheme == "fixture" else [identifier]
    for part in parts:
        _identifier(
            part,
            "evidence_ref",
        )


def _provenance(description, dataset_id):
    # Index validated evidence so later checks can resolve references by ID.
    evidence = {}
    for row in _rows(
        description,
        "provenance",
        dataset_id,
        "evidence_id",
    ):
        _identifier(
            row["kind"],
            "provenance.kind",
        )
        _evidence_reference(row["evidence_ref"])
        if not isinstance(row["description"], str) or not row["description"].strip():
            raise ValueError("provenance.description must be nonempty text")
        if (
            row["evidence_ref"].startswith("fixture:")
            and description["dataset"]["source_kind"] != "fixture"
        ):
            raise ValueError("Fixture evidence cannot verify a real dataset")
        evidence[row["evidence_id"]] = row
    return evidence


def _sources(description, dataset, evidence, warnings):
    sources = _rows(
        description,
        "sources",
        dataset["dataset_id"],
        "platform",
    )
    for source in sources:
        # Check each platform before reconciling it with the dataset total.
        _count(
            source["record_count"],
            "sources.record_count",
        )
        _count(
            source["unknown_dates"],
            "sources.unknown_dates",
        )
        if source["unknown_dates"] > source["record_count"]:
            raise ValueError("Source unknown_dates exceeds its record_count")

        # Collection date and record date answer different questions.
        state = source["collection_state"]
        collected_at = source["latest_collection_at"]
        evidence_id = source["collection_evidence_id"]
        if state == "unknown":
            if collected_at is not None or evidence_id is not None:
                raise ValueError("Unknown collection must leave date and evidence null")
            warnings.append(
                {
                    "code": "collection_date_unknown",
                    "message": f"Collection date is unknown for {source['platform']}.",
                }
            )
        elif state == "verified":
            utc_datetime(
                collected_at,
                "latest_collection_at",
            )
            _identifier(
                evidence_id,
                "collection_evidence_id",
            )
            if (
                evidence_id not in evidence
                or evidence[evidence_id]["kind"] != "collection"
            ):
                raise ValueError("Verified collection requires collection evidence")
        else:
            raise ValueError("collection_state must be unknown or verified")

        if source["platform"] == "unknown":
            warnings.append(
                {
                    "code": "platform_unknown",
                    "message": "Records without a platform remain in the unknown category.",
                }
            )

    # No records may disappear or be counted twice across platforms.
    if sum(row["record_count"] for row in sources) != dataset["row_count"]:
        raise ValueError("Source counts must sum to dataset.row_count")
    if sum(row["unknown_dates"] for row in sources) != dataset["unknown_dates"]:
        raise ValueError("Source unknown_dates must sum to dataset.unknown_dates")


def _months(description, dataset):
    months = _rows(
        description,
        "monthly_counts",
        dataset["dataset_id"],
        "month",
    )
    for row in months:
        _month(row["month"])
        _count(
            row["record_count"],
            "monthly_counts.record_count",
            positive=True,
        )

    # Monthly buckets account only for records whose dates are known.
    dated_count = dataset["row_count"] - dataset["unknown_dates"]
    if sum(row["record_count"] for row in months) != dated_count:
        raise ValueError("Monthly counts plus unknown_dates must sum to row_count")

    # Missing dates cannot supply a coverage interval.
    start, end = dataset["record_start"], dataset["record_end"]
    if dated_count == 0:
        if start is not None or end is not None:
            raise ValueError(
                "A dataset without dated records must have null date bounds"
            )
        return
    start = utc_datetime(
        start,
        "record_start",
    )
    end = utc_datetime(
        end,
        "record_end",
    )
    if start > end:
        raise ValueError("record_start must not follow record_end")
    if dated_count == 1 and start != end:
        raise ValueError("A single dated record must have equal date bounds")

    # The first and last stored months must agree with the record date bounds.
    keys = sorted(row["month"] for row in months)
    if (
        keys[0] != f"{start.year:04d}-{start.month:02d}"
        or keys[-1] != f"{end.year:04d}-{end.month:02d}"
    ):
        raise ValueError("Monthly coverage must match the record date bounds")


def _embeddings(description, dataset, evidence, warnings):
    row = description["embedding_status"]
    _fields(
        row,
        TABLE_COLUMNS["embedding_status"],
        "embedding_status",
    )
    if row["dataset_id"] != dataset["dataset_id"]:
        raise ValueError("embedding_status refers to another dataset")

    # Absence of metadata means unknown, rather than a measured zero coverage.
    optional_fields = (
        "release_id",
        "model",
        "covered_rows",
        "verification_evidence_id",
    )
    if row["state"] == "not_reported":
        if any(row[name] is not None for name in optional_fields):
            raise ValueError("not_reported embeddings must leave optional fields null")
        warnings.append(
            {
                "code": "embeddings_not_reported",
                "message": "Embedding information was not supplied; existence is unknown.",
            }
        )
        return
    if row["state"] not in ("registered", "verified"):
        raise ValueError("Embedding state must be not_reported, registered or verified")

    _identifier(
        row["release_id"],
        "embedding_status.release_id",
    )
    if not isinstance(row["model"], str) or not row["model"].strip():
        raise ValueError("Registered embeddings require a model")
    _count(
        row["covered_rows"],
        "embedding_status.covered_rows",
    )
    if row["covered_rows"] > dataset["row_count"]:
        raise ValueError("Embedding coverage exceeds dataset.row_count")

    # Both references bind this embedding release to this dataset, not another one.
    reference = f"embedding-release:{row['release_id']}"
    if not any(
        item["kind"] == "embedding_registration" and item["evidence_ref"] == reference
        for item in evidence.values()
    ):
        raise ValueError("Registered embeddings require matching registration evidence")
    if row["state"] == "registered":
        if row["verification_evidence_id"] is not None:
            raise ValueError(
                "Registered embeddings must not claim verification evidence"
            )
        warnings.append(
            {
                "code": "embeddings_not_verified",
                "message": "An embedding release is registered; its artifacts are not verified.",
            }
        )
    else:
        _identifier(
            row["verification_evidence_id"],
            "verification_evidence_id",
        )
        verification = evidence.get(row["verification_evidence_id"])
        if (
            verification is None
            or verification["kind"] != "embedding_verification"
            or verification["evidence_ref"] != reference
        ):
            raise ValueError(
                "Verified embeddings require matching verification evidence"
            )

    # Partial coverage is valid, but consumers need to see its limitation.
    if row["covered_rows"] < dataset["row_count"]:
        warnings.append(
            {
                "code": "embeddings_partial_coverage",
                "message": "The reported embedding release covers only part of the dataset.",
            }
        )


def validate_description(description):
    """Validate five normalized table contents and return descriptive warnings.

    dataset and embedding_status are single row dictionaries; provenance,
    sources and monthly_counts are row lists. Publication identity is assigned
    later by the builder. This function does not mutate its input or open files.
    """
    # Validate the shared shape and dataset identity before dependent rows.
    _fields(
        description,
        _DESCRIPTION_FIELDS,
        "description",
    )
    dataset = description["dataset"]
    _fields(
        dataset,
        TABLE_COLUMNS["dataset"],
        "dataset",
    )
    _identifier(
        dataset["dataset_id"],
        "dataset_id",
    )
    _identifier(
        dataset["source_version"],
        "source_version",
    )
    if dataset["source_kind"] not in ("fixture", "personal", "s3"):
        raise ValueError("source_kind must be fixture, personal or s3")
    _count(
        dataset["row_count"],
        "dataset.row_count",
    )
    _count(
        dataset["unknown_dates"],
        "dataset.unknown_dates",
    )
    if dataset["unknown_dates"] > dataset["row_count"]:
        raise ValueError("Dataset unknown_dates exceeds row_count")
    if dataset["prepared_at"] is not None:
        utc_datetime(
            dataset["prepared_at"],
            "prepared_at",
        )

    # Keep valid unknown values and report them separately from errors.
    warnings = []
    if dataset["unknown_dates"]:
        warnings.append(
            {
                "code": "record_dates_missing",
                "message": "Records without dates remain in the total, outside monthly counts.",
            }
        )

    # Resolve evidence first, then reconcile counts and embedding claims.
    evidence = _provenance(
        description,
        dataset["dataset_id"],
    )
    _sources(
        description,
        dataset,
        evidence,
        warnings,
    )
    _months(
        description,
        dataset,
    )
    _embeddings(
        description,
        dataset,
        evidence,
        warnings,
    )

    # A stable order keeps repeated validation results easy to compare.
    return sorted(
        warnings,
        key=lambda item: (item["code"], item["message"]),
    )


def validate_publication(publication, dataset_id):
    """Check package identity separately from dataset identity and query version."""
    _fields(
        publication,
        TABLE_COLUMNS["publication"],
        "publication",
    )
    # Package IDs are canonical UUIDs; they are independent of dataset IDs.
    identifier = publication["publication_id"]
    try:
        valid_uuid = isinstance(identifier, str) and str(UUID(identifier)) == identifier
    except ValueError:
        valid_uuid = False
    if not valid_uuid:
        raise ValueError("publication_id must be a canonical UUID")

    # Tie the package to the expected dataset and supported storage contract.
    if publication["dataset_id"] != dataset_id:
        raise ValueError("publication refers to another dataset")
    if publication["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported publication schema_version")
    utc_datetime(
        publication["created_at"],
        "publication.created_at",
    )


def validate_database(connection):
    """Read and validate all six tables without changing the supplied database.

    This validates row contents. Bundle integrity and physical schema checks
    belong to the bundle reader introduced in the next lessons.
    """
    # Read persisted values so validation also catches mistakes during writing.
    tables = {}
    for table, columns in TABLE_COLUMNS.items():
        # Only fixed schema names enter SQL; no user-provided identifiers are used.
        values = connection.execute(
            f'SELECT {", ".join(columns)} FROM "{table}"'
        ).fetchall()
        tables[table] = [
            dict(
                zip(
                    columns,
                    row,
                    strict=True,
                )
            )
            for row in values
        ]

    # A package must describe one dataset, one embedding status and itself.
    for table in ("dataset", "embedding_status", "publication"):
        if len(tables[table]) != 1:
            raise ValueError(f"{table} must contain exactly one row")

    # Reuse the same logical checks applied to descriptions before writing.
    description = {
        name: tables[name][0]
        if name in ("dataset", "embedding_status")
        else tables[name]
        for name in _DESCRIPTION_FIELDS
    }
    warnings = validate_description(description)
    validate_publication(
        tables["publication"][0],
        description["dataset"]["dataset_id"],
    )
    return warnings
