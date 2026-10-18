"""Storage schema for published dataset state; no topic or sentiment tables yet."""

# Fixed table/column names are also used to read persisted rows for validation.
TABLE_COLUMNS = {
    # Describe the selected dataset and its known temporal bounds.
    "dataset": (
        "dataset_id",
        "source_kind",
        "source_version",
        "row_count",
        "unknown_dates",
        "record_start",
        "record_end",
        "prepared_at",
    ),
    # Keep public evidence references attached to their dataset.
    "provenance": (
        "evidence_id",
        "dataset_id",
        "kind",
        "evidence_ref",
        "description",
    ),
    # Separate platform counts from collection dates and evidence.
    "sources": (
        "dataset_id",
        "platform",
        "record_count",
        "unknown_dates",
        "collection_state",
        "latest_collection_at",
        "collection_evidence_id",
    ),
    # Count dated records by month; unknown dates remain in dataset.
    "monthly_counts": (
        "dataset_id",
        "month",
        "record_count",
    ),
    # Record what is known about embeddings without storing vectors.
    "embedding_status": (
        "dataset_id",
        "state",
        "release_id",
        "model",
        "covered_rows",
        "verification_evidence_id",
    ),
    # Identify this package and the storage schema used to interpret it.
    "publication": (
        "publication_id",
        "dataset_id",
        "schema_version",
        "created_at",
    ),
}

# SQL enforces individual-row constraints; contracts.py checks cross-table meaning.
SCHEMA_SQL = """
CREATE TABLE dataset (
    dataset_id VARCHAR PRIMARY KEY,
    source_kind VARCHAR NOT NULL CHECK (source_kind IN ('fixture', 'personal', 's3')),
    source_version VARCHAR NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count >= 0),
    unknown_dates BIGINT NOT NULL CHECK (unknown_dates BETWEEN 0 AND row_count),
    record_start TIMESTAMPTZ,
    record_end TIMESTAMPTZ,
    prepared_at TIMESTAMPTZ
);

CREATE TABLE provenance (
    evidence_id VARCHAR PRIMARY KEY,
    dataset_id VARCHAR NOT NULL REFERENCES dataset(dataset_id),
    kind VARCHAR NOT NULL,
    evidence_ref VARCHAR NOT NULL,
    description VARCHAR NOT NULL
);

CREATE TABLE sources (
    dataset_id VARCHAR NOT NULL REFERENCES dataset(dataset_id),
    platform VARCHAR NOT NULL,
    record_count BIGINT NOT NULL CHECK (record_count >= 0),
    unknown_dates BIGINT NOT NULL CHECK (unknown_dates BETWEEN 0 AND record_count),
    collection_state VARCHAR NOT NULL CHECK (collection_state IN ('verified', 'unknown')),
    latest_collection_at TIMESTAMPTZ,
    collection_evidence_id VARCHAR REFERENCES provenance(evidence_id),
    PRIMARY KEY (dataset_id, platform)
);

CREATE TABLE monthly_counts (
    dataset_id VARCHAR NOT NULL REFERENCES dataset(dataset_id),
    month VARCHAR NOT NULL,
    record_count BIGINT NOT NULL CHECK (record_count > 0),
    PRIMARY KEY (dataset_id, month)
);

CREATE TABLE embedding_status (
    dataset_id VARCHAR PRIMARY KEY REFERENCES dataset(dataset_id),
    state VARCHAR NOT NULL CHECK (state IN ('not_reported', 'registered', 'verified')),
    release_id VARCHAR,
    model VARCHAR,
    covered_rows BIGINT CHECK (covered_rows >= 0),
    verification_evidence_id VARCHAR REFERENCES provenance(evidence_id)
);

CREATE TABLE publication (
    publication_id VARCHAR PRIMARY KEY,
    dataset_id VARCHAR NOT NULL REFERENCES dataset(dataset_id),
    schema_version VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
"""
