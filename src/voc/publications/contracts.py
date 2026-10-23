"""Logical contracts for published dataset state, independent of ML and web tools.

Descriptions contain normalized table rows, not raw reviews. Validation checks
consistency and evidence links; the exporter must verify real source evidence.
"""

import json
import re
from datetime import UTC, date, datetime
from uuid import UUID

from .schema import (
    TABLE_COLUMNS,
    TABLE_COLUMNS_BY_VERSION,
    TABLE_COLUMNS_V2,
    V2_TABLE_COLUMNS,
)

SCHEMA_VERSION = "voc-data-state-v1"
SCHEMA_VERSION_V2 = "voc-data-state-v2"
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION, SCHEMA_VERSION_V2)
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
    "analysis-candidate",
)
_ANALYSIS_FIELDS = tuple(V2_TABLE_COLUMNS)
_DIGEST = re.compile(r"[0-9a-f]{64}")


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
        # The superset holds every table of every version; v1 tables are unchanged.
        _fields(
            row,
            TABLE_COLUMNS_V2[table],
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


def _json_text(value, label, *, expect=dict):
    # Nested structures travel as canonical JSON text; the shape is checked here.
    if not isinstance(value, str):
        raise ValueError(f"{label} must be JSON text")  # noqa: TRY004 - uniform contract errors
    try:
        parsed = json.loads(value)
    except ValueError as error:
        raise ValueError(f"{label} must be valid JSON") from error
    if not isinstance(parsed, expect):
        raise ValueError(f"{label} must hold a JSON {expect.__name__}")  # noqa: TRY004 - uniform contract errors
    return parsed


def _digest(value, label):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 hex digest")


def _review_rows(block, table, dataset_id):
    """Rows keyed by review ID: collector IDs such as ``appbot:1:2`` are opaque text."""
    rows = block[table]
    if not isinstance(rows, list):
        raise ValueError(f"{table} must be a list of rows")  # noqa: TRY004 - uniform contract errors
    seen = set()
    for row in rows:
        _fields(row, TABLE_COLUMNS_V2[table], table)
        if row["dataset_id"] != dataset_id:
            raise ValueError(f"{table} refers to another dataset")
        record_id = row["record_id"]
        if (
            not isinstance(record_id, str)
            or not record_id.strip()
            or len(record_id) > 512
            or any(c.isspace() or ord(c) < 32 for c in record_id)
        ):
            raise ValueError(f"{table}.record_id must be a nonblank identifier")
        if record_id in seen:
            raise ValueError(f"{table} contains a duplicate record_id")
        seen.add(record_id)
    return rows


def _analysis(description, dataset, evidence, warnings):
    """Validate the v2 block: one candidate whose results cover its selection."""
    from voc.analysis.contracts import (
        Keyword,
        ReviewAssignment,
        ReviewSentiment,
        TopicRecord,
        validate_results,
    )

    block = description["analysis"]
    _fields(block, _ANALYSIS_FIELDS, "analysis")
    dataset_id = dataset["dataset_id"]
    row = block["analysis"]
    _fields(row, V2_TABLE_COLUMNS["analysis"], "analysis.analysis")
    if row["dataset_id"] != dataset_id:
        raise ValueError("analysis refers to another dataset")
    for name in ("analysis_id", "embedding_release_id"):
        _identifier(row[name], "analysis." + name)
    if row["parent_analysis_id"] is not None:
        _identifier(row["parent_analysis_id"], "analysis.parent_analysis_id")
    if not isinstance(row["embedding_profile"], str) or not row["embedding_profile"]:
        raise ValueError("analysis.embedding_profile must be text")
    if row["selection_kind"] not in ("full", "first-n", "stratified"):
        raise ValueError("analysis.selection_kind must be full, first-n or stratified")
    for name in (
        "selection_size",
        "selected_reviews",
        "assigned_reviews",
        "unassigned_reviews",
        "topic_count",
        "classified",
        "excluded",
        "failed",
    ):
        _count(row[name], "analysis." + name, positive=name.startswith("select"))
    for name in ("code_sha256", "manifest_sha256"):
        _digest(row[name], "analysis." + name)
    _json_text(row["configuration_json"], "analysis.configuration_json")
    utc_datetime(row["created_at"], "analysis.created_at")
    if row["selection_size"] != row["selected_reviews"]:
        raise ValueError("analysis.selection_size must equal selected_reviews")
    if row["selected_reviews"] > dataset["row_count"]:
        raise ValueError("Analysis selection exceeds dataset.row_count")
    if (
        row["selection_kind"] == "full"
        and row["selected_reviews"] != dataset["row_count"]
    ):
        raise ValueError("A full selection must cover every dataset row")

    # The candidate was built on the registered embedding release, not another.
    status = description["embedding_status"]
    if (
        status["state"] == "not_reported"
        or status["release_id"] != row["embedding_release_id"]
    ):
        raise ValueError("analysis.embedding_release_id must match embedding_status")
    reference = f"analysis-candidate:{row['analysis_id']}"
    if not any(
        item["kind"] == "analysis_candidate" and item["evidence_ref"] == reference
        for item in evidence.values()
    ):
        raise ValueError("An analysis requires analysis_candidate provenance")

    reviews = _review_rows(block, "reviews", dataset_id)
    if len(reviews) != row["selected_reviews"]:
        raise ValueError("reviews must contain one row per selected review")
    positions = set()
    for item in reviews:
        if not isinstance(item["platform"], str) or not item["platform"].strip():
            raise ValueError("reviews.platform must be text")
        if item["record_date"] is not None and not isinstance(item["record_date"], str):
            raise ValueError("reviews.record_date must be text or null")
        if not isinstance(item["model_text"], str) or not item["model_text"].strip():
            raise ValueError("reviews.model_text must be nonblank text")
        _count(item["vector_row"], "reviews.vector_row")
        if item["vector_row"] in positions:
            raise ValueError("reviews.vector_row must be unique")
        positions.add(item["vector_row"])
    record_ids = [item["record_id"] for item in reviews]

    assignments = _review_rows(block, "assignments", dataset_id)
    sentiments = _review_rows(block, "sentiment", dataset_id)
    topics = []
    seen_topics = set()
    for item in block["topics"]:
        _fields(item, V2_TABLE_COLUMNS["topics"], "topics")
        if item["dataset_id"] != dataset_id:
            raise ValueError("topics refers to another dataset")
        if type(item["topic_id"]) is not int or item["topic_id"] in seen_topics:
            raise ValueError("topics.topic_id must be a unique integer")
        seen_topics.add(item["topic_id"])
        _count(item["member_count"], "topics.member_count")
        keywords = _json_text(
            item["keywords_json"], "topics.keywords_json", expect=list
        )
        evidence_ids = _json_text(
            item["evidence_ids_json"], "topics.evidence_ids_json", expect=list
        )
        try:
            topics.append(
                TopicRecord(
                    item["topic_id"],
                    tuple(Keyword(k["term"], k["weight"]) for k in keywords),
                    tuple(evidence_ids),
                    item["name"],
                    item["description"],
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid topic record: {error}") from error
    try:
        assignment_records = [
            ReviewAssignment(a["record_id"], a["topic_id"], a["membership_strength"])
            for a in assignments
        ]
        sentiment_records = [
            ReviewSentiment(
                s["record_id"],
                s["status"],
                s["model_label"],
                None
                if s["status"] != "classified"
                else {k: s[k] for k in ("negative", "neutral", "positive")},
                s["reason"],
                s["input_sha256"],
            )
            for s in sentiments
        ]
        coverage = validate_results(
            record_ids, assignment_records, topics, sentiment_records
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Analysis results do not reconcile: {error}") from error
    expected = {
        "selected_reviews": row["selected_reviews"],
        "assigned_reviews": row["assigned_reviews"],
        "unassigned_reviews": row["unassigned_reviews"],
        "topic_count": row["topic_count"],
        "classified": row["classified"],
        "excluded": row["excluded"],
        "failed": row["failed"],
    }
    if coverage != expected:
        raise ValueError("analysis counts differ from the stored results")
    for item in sentiments:
        if item["status"] != "classified" and any(
            item[k] is not None for k in ("negative", "neutral", "positive")
        ):
            raise ValueError("Unclassified sentiment must not carry probabilities")
    members = {}
    for a in assignments:
        members.setdefault(a["topic_id"], set()).add(a["record_id"])
    for item in block["topics"]:
        if item["member_count"] != len(members.get(item["topic_id"], ())):
            raise ValueError("topics.member_count differs from the assignments")

    completed = 0
    seen_interpretations = set()
    for item in block["interpretations"]:
        _fields(item, V2_TABLE_COLUMNS["interpretations"], "interpretations")
        if item["dataset_id"] != dataset_id:
            raise ValueError("interpretations refers to another dataset")
        tid = item["topic_id"]
        if (
            type(tid) is not int
            or tid < 0
            or tid not in seen_topics
            or tid in seen_interpretations
        ):
            raise ValueError("interpretations must target one distinct assigned topic")
        seen_interpretations.add(tid)
        for name in ("requested_model", "prompt_version", "attempt_id"):
            if not isinstance(item[name], str) or not item[name]:
                raise ValueError(f"interpretations.{name} must be text")
        utc_datetime(item["created_at"], "interpretations.created_at")
        if item["status"] == "completed":
            if item["assessment"] not in ("single_feature", "mixed", "unclear"):
                raise ValueError("Completed interpretations require an assessment")
            for name in ("name", "topic_description"):
                if not isinstance(item[name], str) or not item[name].strip():
                    raise ValueError(f"Completed interpretations require {name}")
            if (item["assessment"] == "single_feature") != isinstance(
                item["proposed_feature"], str
            ):
                raise ValueError("Only single-feature topics propose a feature")
            ids = _json_text(
                item["supporting_review_ids_json"],
                "supporting_review_ids_json",
                expect=list,
            )
            if not ids or not set(ids) <= members.get(tid, set()):
                raise ValueError(
                    "Supporting reviews must belong to the interpreted topic"
                )
            completed += 1
        elif item["status"] == "failed":
            if any(
                item[k] is not None
                for k in (
                    "name",
                    "topic_description",
                    "proposed_feature",
                    "assessment",
                    "supporting_review_ids_json",
                )
            ):
                raise ValueError("Failed interpretations carry no result")
        else:
            raise ValueError("interpretations.status must be completed or failed")

    if row["selected_reviews"] < dataset["row_count"]:
        warnings.append(
            {
                "code": "analysis_partial_selection",
                "message": "The analysis covers a selection of the dataset, not every review.",
            }
        )
    if row["excluded"] or row["failed"]:
        warnings.append(
            {
                "code": "sentiment_unclassified",
                "message": "Some reviews have no sentiment label; they are counted by status.",
            }
        )
    if completed < row["topic_count"]:
        warnings.append(
            {
                "code": "interpretations_partial",
                "message": "Not every topic has a completed interpretation; the rest keep keyword names.",
            }
        )


def validate_description(description):
    """Validate the normalized table contents and return descriptive warnings.

    dataset and embedding_status are single row dictionaries; provenance,
    sources and monthly_counts are row lists. A v2 description adds an
    ``analysis`` block holding one candidate's tables. Publication identity is
    assigned later by the builder. This function does not mutate its input.
    """
    # Validate the shared shape and dataset identity before dependent rows.
    _fields(
        description,
        (*_DESCRIPTION_FIELDS, "analysis")
        if isinstance(description, dict) and "analysis" in description
        else _DESCRIPTION_FIELDS,
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
    if "analysis" in description:
        _analysis(description, dataset, evidence, warnings)

    # A stable order keeps repeated validation results easy to compare.
    return sorted(
        warnings,
        key=lambda item: (item["code"], item["message"]),
    )


def schema_version_for(description):
    """The storage version a description needs: v2 when it carries an analysis."""
    return SCHEMA_VERSION_V2 if "analysis" in description else SCHEMA_VERSION


def validate_publication(publication, dataset_id, schema_version=None):
    """Check package identity separately from dataset identity and query version.

    ``schema_version`` pins the expected storage version; without it, any
    supported version is accepted.
    """
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
    if publication["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError("Unsupported publication schema_version")
    if schema_version is not None and publication["schema_version"] != schema_version:
        raise ValueError("Publication schema_version differs from the expected version")
    utc_datetime(
        publication["created_at"],
        "publication.created_at",
    )


def validate_database(connection):
    """Read and validate every table of the stored version without changing it.

    This validates row contents. Bundle integrity and physical schema checks
    belong to the bundle reader.
    """
    # The publication row names the version. Anything else, a missing row, two
    # rows or an unknown version, is read as v1 so the row checks below report it.
    versions = connection.execute("SELECT schema_version FROM publication").fetchall()
    version = SCHEMA_VERSION
    if len(versions) == 1 and versions[0][0] in TABLE_COLUMNS_BY_VERSION:
        version = versions[0][0]
    if version == SCHEMA_VERSION_V2:
        present = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = current_database()"
            ).fetchall()
        }
        if not set(V2_TABLE_COLUMNS) <= present:
            raise ValueError("publication claims voc-data-state-v2 without its tables")
    tables = {}
    for table, columns in TABLE_COLUMNS_BY_VERSION[version].items():
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
    if version == SCHEMA_VERSION_V2:
        if len(tables["analysis"]) != 1:
            raise ValueError("analysis must contain exactly one row")
        description["analysis"] = {
            name: tables[name][0] if name == "analysis" else tables[name]
            for name in _ANALYSIS_FIELDS
        }
        # Timestamps come back as datetimes; the description contract takes either.
    warnings = validate_description(description)
    validate_publication(
        tables["publication"][0],
        description["dataset"]["dataset_id"],
        version,
    )
    return warnings
