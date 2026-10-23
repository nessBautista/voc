"""Storage schema for published dataset state, in two frozen versions.

Version 1 describes a dataset. Version 2 adds the tables of one analysis
candidate: reviews, assignments, topics, sentiment and interpretations. The v1
SQL never changes; a v2 package is v1 plus the extra tables.
"""

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

# Version 1 stays byte-for-byte what it was; these names keep old imports working.
SCHEMA_SQL_V1 = SCHEMA_SQL
TABLE_COLUMNS_V1 = TABLE_COLUMNS

# Version 2: one analysis candidate, joined to its dataset by dataset_id.
V2_TABLE_COLUMNS = {
    "analysis": (
        "dataset_id",
        "analysis_id",
        "parent_analysis_id",
        "embedding_release_id",
        "embedding_profile",
        "selection_kind",
        "selection_size",
        "selected_reviews",
        "assigned_reviews",
        "unassigned_reviews",
        "topic_count",
        "classified",
        "excluded",
        "failed",
        "configuration_json",
        "code_sha256",
        "manifest_sha256",
        "created_at",
    ),
    "reviews": (
        "dataset_id",
        "record_id",
        "platform",
        "record_date",
        "model_text",
        "vector_row",
    ),
    "assignments": (
        "dataset_id",
        "record_id",
        "topic_id",
        "membership_strength",
    ),
    "topics": (
        "dataset_id",
        "topic_id",
        "name",
        "description",
        "keywords_json",
        "evidence_ids_json",
        "member_count",
    ),
    "sentiment": (
        "dataset_id",
        "record_id",
        "status",
        "model_label",
        "negative",
        "neutral",
        "positive",
        "reason",
        "input_sha256",
    ),
    "interpretations": (
        "dataset_id",
        "topic_id",
        "status",
        "name",
        "topic_description",
        "proposed_feature",
        "assessment",
        "supporting_review_ids_json",
        "requested_model",
        "prompt_version",
        "attempt_id",
        "created_at",
    ),
}
TABLE_COLUMNS_V2 = {**TABLE_COLUMNS_V1, **V2_TABLE_COLUMNS}

V2_TABLES_SQL = """
CREATE TABLE analysis (
    dataset_id VARCHAR PRIMARY KEY REFERENCES dataset(dataset_id),
    analysis_id VARCHAR NOT NULL,
    parent_analysis_id VARCHAR,
    embedding_release_id VARCHAR NOT NULL,
    embedding_profile VARCHAR NOT NULL,
    selection_kind VARCHAR NOT NULL CHECK (selection_kind IN ('full', 'first-n', 'stratified')),
    selection_size BIGINT NOT NULL CHECK (selection_size > 0),
    selected_reviews BIGINT NOT NULL CHECK (selected_reviews > 0),
    assigned_reviews BIGINT NOT NULL CHECK (assigned_reviews >= 0),
    unassigned_reviews BIGINT NOT NULL CHECK (unassigned_reviews >= 0),
    topic_count BIGINT NOT NULL CHECK (topic_count >= 0),
    classified BIGINT NOT NULL CHECK (classified >= 0),
    excluded BIGINT NOT NULL CHECK (excluded >= 0),
    failed BIGINT NOT NULL CHECK (failed >= 0),
    configuration_json VARCHAR NOT NULL,
    code_sha256 VARCHAR NOT NULL,
    manifest_sha256 VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE reviews (
    dataset_id VARCHAR NOT NULL REFERENCES dataset(dataset_id),
    record_id VARCHAR NOT NULL,
    platform VARCHAR NOT NULL,
    record_date VARCHAR,
    model_text VARCHAR NOT NULL,
    vector_row BIGINT NOT NULL CHECK (vector_row >= 0),
    PRIMARY KEY (dataset_id, record_id)
);

CREATE TABLE assignments (
    dataset_id VARCHAR NOT NULL,
    record_id VARCHAR NOT NULL,
    topic_id INTEGER NOT NULL CHECK (topic_id >= -1),
    membership_strength DOUBLE NOT NULL CHECK (membership_strength BETWEEN 0 AND 1),
    PRIMARY KEY (dataset_id, record_id),
    FOREIGN KEY (dataset_id, record_id) REFERENCES reviews(dataset_id, record_id)
);

CREATE TABLE topics (
    dataset_id VARCHAR NOT NULL REFERENCES dataset(dataset_id),
    topic_id INTEGER NOT NULL CHECK (topic_id >= -1),
    name VARCHAR NOT NULL,
    description VARCHAR NOT NULL,
    keywords_json VARCHAR NOT NULL,
    evidence_ids_json VARCHAR NOT NULL,
    member_count BIGINT NOT NULL CHECK (member_count >= 0),
    PRIMARY KEY (dataset_id, topic_id)
);

CREATE TABLE sentiment (
    dataset_id VARCHAR NOT NULL,
    record_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('classified', 'excluded', 'failed')),
    model_label VARCHAR CHECK (model_label IN ('negative', 'neutral', 'positive')),
    negative DOUBLE CHECK (negative BETWEEN 0 AND 1),
    neutral DOUBLE CHECK (neutral BETWEEN 0 AND 1),
    positive DOUBLE CHECK (positive BETWEEN 0 AND 1),
    reason VARCHAR,
    input_sha256 VARCHAR,
    PRIMARY KEY (dataset_id, record_id),
    FOREIGN KEY (dataset_id, record_id) REFERENCES reviews(dataset_id, record_id)
);

CREATE TABLE interpretations (
    dataset_id VARCHAR NOT NULL,
    topic_id INTEGER NOT NULL CHECK (topic_id >= 0),
    status VARCHAR NOT NULL CHECK (status IN ('completed', 'failed')),
    name VARCHAR,
    topic_description VARCHAR,
    proposed_feature VARCHAR,
    assessment VARCHAR CHECK (assessment IN ('single_feature', 'mixed', 'unclear')),
    supporting_review_ids_json VARCHAR,
    requested_model VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    attempt_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (dataset_id, topic_id),
    FOREIGN KEY (dataset_id, topic_id) REFERENCES topics(dataset_id, topic_id)
);
"""
SCHEMA_SQL_V2 = SCHEMA_SQL_V1 + V2_TABLES_SQL

SCHEMA_SQL_BY_VERSION = {
    "voc-data-state-v1": SCHEMA_SQL_V1,
    "voc-data-state-v2": SCHEMA_SQL_V2,
}
TABLE_COLUMNS_BY_VERSION = {
    "voc-data-state-v1": TABLE_COLUMNS_V1,
    "voc-data-state-v2": TABLE_COLUMNS_V2,
}
