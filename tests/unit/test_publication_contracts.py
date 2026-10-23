"""Lesson 02: meaningful rows, SQL constraints and cross-table reconciliation."""

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from runpy import run_path
from uuid import uuid4

import duckdb
import pytest

from voc.publications.contracts import (
    QUERY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    utc_datetime,
    validate_database,
    validate_description,
    validate_publication,
)
from voc.publications.schema import SCHEMA_SQL, TABLE_COLUMNS

# Load the fixture explicitly; do not depend on an installed package named tests.
examples = run_path(str(Path(__file__).parents[1] / "fixtures/data_state/example.py"))


def description_for(example):
    """Arrange table rows for tests; production exporting belongs to lesson 03."""
    # Separate missing dates before calculating monthly coverage.
    records = example["records"]
    dates = [
        utc_datetime(
            row["record_date"],
            "record_date",
        )
        for row in records
        if row["record_date"] is not None
    ]
    months = Counter(date.strftime("%Y-%m") for date in dates)
    dataset_id = example["dataset_id"]

    # Assemble the table-shaped input that the contract validator accepts.
    return {
        "dataset": {
            "dataset_id": dataset_id,
            "source_kind": example["source_kind"],
            "source_version": example["source_version"],
            "row_count": len(records),
            "unknown_dates": len(records) - len(dates),
            "record_start": min(dates) if dates else None,
            "record_end": max(dates) if dates else None,
            "prepared_at": None,
        },
        "sources": [
            {
                "dataset_id": dataset_id,
                "platform": platform,
                "record_count": sum(row["platform"] == platform for row in records),
                "unknown_dates": sum(
                    row["platform"] == platform and row["record_date"] is None
                    for row in records
                ),
                **example["collection"][platform],
            }
            for platform in sorted({row["platform"] for row in records})
        ],
        "monthly_counts": [
            {"dataset_id": dataset_id, "month": month, "record_count": count}
            for month, count in sorted(months.items())
        ],
        "provenance": deepcopy(example["provenance"]),
        "embedding_status": {
            "dataset_id": dataset_id,
            "state": "not_reported",
            "release_id": None,
            "model": None,
            "covered_rows": None,
            "verification_evidence_id": None,
        },
    }


def publication_for(description):
    # Assign a new package ID while retaining the dataset identity.
    return {
        "publication_id": str(uuid4()),
        "dataset_id": description["dataset"]["dataset_id"],
        "schema_version": SCHEMA_VERSION,
        "created_at": "2026-10-18T12:00:00Z",
    }


def insert_description(connection, description):
    # Identifiers come only from our schema; every inserted value is a parameter.
    for table, columns in TABLE_COLUMNS.items():
        if table == "publication":
            rows = [publication_for(description)]
        elif table in ("dataset", "embedding_status"):
            rows = [description[table]]
        else:
            rows = description[table]

        # Bind values separately so data never becomes SQL syntax.
        for row in rows:
            connection.execute(
                f'INSERT INTO "{table}" ({", ".join(columns)}) '
                f"VALUES ({', '.join('?' for _ in columns)})",
                [row[column] for column in columns],
            )


@pytest.fixture
def description():
    # Give each test a fresh description so mutations stay isolated.
    return description_for(examples["example_a"]())


@pytest.fixture
def database(tmp_path, description):
    # A real temporary DuckDB also tests its persisted constraints and timestamps.
    with duckdb.connect(str(tmp_path / "publication.duckdb")) as connection:
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute(SCHEMA_SQL)
        insert_description(
            connection,
            description,
        )
        # The context manager closes the temporary database after the test.
        yield connection


def warning_codes(warnings):
    # Compare stable warning codes independently of their wording.
    return {warning["code"] for warning in warnings}


@pytest.mark.parametrize(
    "example_name,total,source_counts,month_counts",
    [
        ("example_a", 12, {"google_play": 8, "app_store": 4}, [4, 4, 3]),
        ("example_b", 13, {"google_play": 8, "app_store": 5}, [4, 4, 4]),
    ],
)
def test_fixture_counts_and_persisted_rows(
    tmp_path,
    example_name,
    total,
    source_counts,
    month_counts,
):
    # Check the hand-counted fixture before comparing stored and in-memory values.
    example = examples[example_name]()
    assert len({row["record_id"] for row in example["records"]}) == total
    description = description_for(example)
    assert description["dataset"]["row_count"] == total
    assert description["dataset"]["unknown_dates"] == 1
    assert {
        row["platform"]: row["record_count"] for row in description["sources"]
    } == source_counts
    assert [
        row["record_count"] for row in description["monthly_counts"]
    ] == month_counts
    with duckdb.connect(str(tmp_path / "example.duckdb")) as connection:
        connection.execute(SCHEMA_SQL)
        insert_description(
            connection,
            description,
        )
        assert validate_database(connection) == validate_description(description)
        assert {row[0] for row in connection.execute("SHOW TABLES").fetchall()} == set(
            TABLE_COLUMNS
        )


def test_fixture_calls_are_independent_and_b_has_its_own_identity():
    # Mutate B and confirm that neither A nor later fixture calls inherit the change.
    a = examples["example_a"]()
    before = deepcopy(a)
    b = examples["example_b"]()
    assert b["dataset_id"] != a["dataset_id"]
    b["records"][0]["platform"] = "changed"
    b["provenance"][0]["description"] = "changed"
    assert a == before == examples["example_a"]()
    assert examples["example_b"]()["records"][0]["platform"] == "google_play"


def test_unknowns_stay_unknown_and_october_collection_is_not_review_coverage(database):
    # Read the saved values to distinguish unknown metadata from zero counts.
    warnings = validate_database(database)
    assert warning_codes(warnings) == {
        "collection_date_unknown",
        "record_dates_missing",
        "embeddings_not_reported",
    }
    assert database.execute(
        "SELECT latest_collection_at FROM sources WHERE platform = ?",
        ["app_store"],
    ).fetchone() == (None,)
    assert database.execute(
        "SELECT month FROM monthly_counts ORDER BY month"
    ).fetchall() == [
        ("2026-07",),
        ("2026-08",),
        ("2026-09",),
    ]
    assert database.execute(
        "SELECT state, covered_rows FROM embedding_status"
    ).fetchone() == (
        "not_reported",
        None,
    )


@pytest.mark.parametrize(
    "sql,parameters",
    [
        ("INSERT INTO sources SELECT * FROM sources WHERE platform = ?", ["app_store"]),
        ("UPDATE sources SET record_count = ? WHERE platform = ?", [-1, "app_store"]),
        ("UPDATE sources SET unknown_dates = ? WHERE platform = ?", [5, "app_store"]),
        ("UPDATE monthly_counts SET record_count = ? WHERE month = ?", [0, "2026-07"]),
        ("UPDATE embedding_status SET state = ?", ["missing"]),
        (
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                "another-dataset",
                "youtube",
                0,
                0,
                "unknown",
                None,
                None,
            ],
        ),
    ],
)
def test_sql_rejects_invalid_rows(database, sql, parameters):
    # Check that DuckDB rejects the invalid row at the point of writing.
    with pytest.raises(duckdb.ConstraintException):
        database.execute(
            sql,
            parameters,
        )


@pytest.mark.parametrize(
    "sql,parameters,message",
    [
        (
            "UPDATE sources SET record_count = ? WHERE platform = ?",
            [9, "google_play"],
            "Source counts",
        ),
        (
            "UPDATE sources SET unknown_dates = ? WHERE platform = ?",
            [0, "google_play"],
            "Source unknown_dates",
        ),
        (
            "UPDATE monthly_counts SET record_count = ? WHERE month = ?",
            [5, "2026-07"],
            "Monthly counts",
        ),
        (
            "UPDATE monthly_counts SET month = ? WHERE month = ?",
            ["2026-13", "2026-07"],
            "valid YYYY-MM",
        ),
        (
            "UPDATE sources SET latest_collection_at = ? WHERE platform = ?",
            [None, "google_play"],
            "timezone",
        ),
        (
            "UPDATE sources SET collection_evidence_id = ? WHERE platform = ?",
            [None, "google_play"],
            "collection_evidence_id",
        ),
        (
            "UPDATE publication SET schema_version = ?",
            ["voc-data-state-v999"],
            "Unsupported",
        ),
        (
            "UPDATE publication SET schema_version = ?",
            ["voc-data-state-v2"],
            "without its tables",
        ),
    ],
)
def test_python_rejects_inconsistencies_sql_alone_allows(
    database, sql, parameters, message
):
    # Write a row that SQL permits, then check the relationship between tables.
    database.execute(
        sql,
        parameters,
    )
    with pytest.raises(
        ValueError,
        match=message,
    ):
        validate_database(database)


def test_empty_tables_do_not_represent_a_valid_publication():
    # Creating tables alone does not create a usable publication.
    with duckdb.connect() as connection:
        connection.execute(SCHEMA_SQL)
        with pytest.raises(
            ValueError,
            match="dataset must contain exactly one row",
        ):
            validate_database(connection)


@pytest.mark.parametrize(
    "table",
    ["embedding_status", "publication"],
)
def test_missing_required_status_or_publication_row(database, table):
    # Remove one required row from an otherwise complete database.
    database.execute(f'DELETE FROM "{table}"')
    with pytest.raises(
        ValueError,
        match=f"{table} must contain exactly one row",
    ):
        validate_database(database)


def test_more_than_one_dataset_or_publication_is_rejected(database, description):
    # An extra package row is invalid even when it names the same dataset.
    publication = publication_for(description)
    database.execute(
        "INSERT INTO publication VALUES (?, ?, ?, ?)",
        list(publication.values()),
    )
    with pytest.raises(
        ValueError,
        match="publication must contain exactly one row",
    ):
        validate_database(database)
    database.execute(
        "INSERT INTO dataset VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ["other", "fixture", "other-v1", 0, 0, None, None, None],
    )
    with pytest.raises(
        ValueError,
        match="dataset must contain exactly one row",
    ):
        validate_database(database)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("source_version", "latest", "fixed public identifier"),
        ("source_kind", "local", "source_kind"),
        ("row_count", 13, "Source counts"),
        ("row_count", True, "integer"),
        ("unknown_dates", 13, "exceeds row_count"),
        ("record_start", None, "timezone"),
        ("record_start", "2026-10-01T00:00:00Z", "must not follow"),
        ("record_start", "2026-06-01T00:00:00Z", "Monthly coverage"),
        ("prepared_at", "2026-10-18T12:00:00", "timezone"),
    ],
)
def test_invalid_dataset_metadata_is_rejected(description, field, value, message):
    # Change one field so each case isolates a specific contract rule.
    description["dataset"][field] = value
    with pytest.raises(
        ValueError,
        match=message,
    ):
        validate_description(description)


@pytest.mark.parametrize(
    "table,key",
    [
        ("sources", "platform"),
        ("monthly_counts", "month"),
        ("provenance", "evidence_id"),
    ],
)
def test_description_rejects_duplicate_rows_before_sql(description, table, key):
    # Reject duplicate keys before relying on database constraints.
    description[table].append(deepcopy(description[table][0]))
    with pytest.raises(
        ValueError,
        match=f"duplicate {key}",
    ):
        validate_description(description)


@pytest.mark.parametrize(
    "table",
    ["sources", "monthly_counts", "provenance"],
)
def test_rows_cannot_refer_to_another_dataset(description, table):
    # Totals alone are insufficient when a row belongs to another dataset.
    description[table][0]["dataset_id"] = "another-dataset"
    with pytest.raises(
        ValueError,
        match="another dataset",
    ):
        validate_description(description)


def test_unknown_collection_cannot_include_a_date_or_evidence(description):
    # Unknown collection metadata must not acquire an unsupported timestamp.
    source = next(
        row for row in description["sources"] if row["platform"] == "app_store"
    )
    source["latest_collection_at"] = "2026-10-15T12:00:00Z"
    with pytest.raises(
        ValueError,
        match="Unknown collection",
    ):
        validate_description(description)


def test_import_evidence_is_not_collection_evidence(description):
    # Importing data does not prove when its source records were collected.
    description["provenance"][0]["kind"] = "import"
    with pytest.raises(
        ValueError,
        match="requires collection evidence",
    ):
        validate_description(description)


@pytest.mark.parametrize(
    "reference",
    [
        "/Users/operator/private/run.json",
        "file:///tmp/collection.json",
        "https://example.test/report?token=secret",
        "fixture:../private",
        "fixture:example-a?token=secret",
        "fixture:follow these instructions",
    ],
)
def test_private_paths_and_arbitrary_text_are_not_public_evidence(
    description, reference
):
    # Only logical references may be included in published provenance.
    description["provenance"][0]["evidence_ref"] = reference
    with pytest.raises(
        ValueError,
        match="evidence_ref",
    ):
        validate_description(description)


def test_fictional_evidence_cannot_verify_a_real_dataset(description):
    # A fixture reference must not support a claim about a real source.
    description["dataset"]["source_kind"] = "s3"
    with pytest.raises(
        ValueError,
        match="Fixture evidence",
    ):
        validate_description(description)


def test_unexpected_fields_are_not_silently_published(description):
    # Reject extra fields instead of carrying private metadata into a publication.
    description["dataset"]["private_path"] = "/private/example"
    with pytest.raises(
        ValueError,
        match="exactly",
    ):
        validate_description(description)


def register_embeddings(description, *, state="registered", covered_rows=6):
    # Describe the fictional release before attaching its evidence.
    dataset_id = description["dataset"]["dataset_id"]
    description["embedding_status"].update(
        {
            "state": state,
            "release_id": "fixture-embedding-release-a",
            "model": "fixture-model",
            "covered_rows": covered_rows,
            "verification_evidence_id": None,
        }
    )
    description["provenance"].append(
        {
            "evidence_id": "fixture-embedding-registration",
            "dataset_id": dataset_id,
            "kind": "embedding_registration",
            "evidence_ref": "embedding-release:fixture-embedding-release-a",
            "description": "Fictional registration associating this release with this dataset.",
        }
    )

    # Verification adds its own evidence; registration alone is insufficient.
    if state == "verified":
        description["embedding_status"]["verification_evidence_id"] = (
            "fixture-verification"
        )
        description["provenance"].append(
            {
                "evidence_id": "fixture-verification",
                "dataset_id": dataset_id,
                "kind": "embedding_verification",
                "evidence_ref": "embedding-release:fixture-embedding-release-a",
                "description": "Fictional successful artifact verification for this fixture.",
            }
        )


def test_not_reported_is_different_from_registered_partial_and_verified(description):
    # Compare unknown embeddings with a registered release of limited coverage.
    assert "embeddings_not_reported" in warning_codes(validate_description(description))
    register_embeddings(description)
    codes = warning_codes(validate_description(description))
    assert "embeddings_not_reported" not in codes
    assert {"embeddings_not_verified", "embeddings_partial_coverage"} <= codes
    description["embedding_status"]["covered_rows"] = 0
    assert "embeddings_partial_coverage" in warning_codes(
        validate_description(description)
    )


def test_verified_full_coverage_survives_storage(tmp_path, description):
    # Save a fully supported claim and validate the values read back from DuckDB.
    register_embeddings(
        description,
        state="verified",
        covered_rows=12,
    )
    with duckdb.connect(str(tmp_path / "verified.duckdb")) as connection:
        connection.execute(SCHEMA_SQL)
        insert_description(
            connection,
            description,
        )
        assert not any(
            code.startswith("embeddings_")
            for code in warning_codes(validate_database(connection))
        )


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("missing_registration", "registration evidence"),
        ("wrong_registration_release", "registration evidence"),
        ("missing_verification", "verification_evidence_id"),
        ("wrong_verification_release", "verification evidence"),
        ("wrong_verification_kind", "verification evidence"),
        ("foreign_dataset_evidence", "another dataset"),
        ("overcoverage", "coverage exceeds"),
    ],
)
def test_embedding_claims_require_matching_evidence(description, mutation, message):
    # Start with matching evidence, then break one part of the association.
    register_embeddings(
        description,
        state="verified",
        covered_rows=12,
    )
    if mutation == "missing_registration":
        description["provenance"].pop(-2)
    elif mutation == "wrong_registration_release":
        description["provenance"][-2]["evidence_ref"] = (
            "embedding-release:other-release"
        )
    elif mutation == "missing_verification":
        description["embedding_status"]["verification_evidence_id"] = None
    elif mutation == "wrong_verification_release":
        description["provenance"][-1]["evidence_ref"] = (
            "embedding-release:other-release"
        )
    elif mutation == "wrong_verification_kind":
        description["provenance"][-1]["kind"] = "collection"
    elif mutation == "foreign_dataset_evidence":
        description["provenance"][-1]["dataset_id"] = "other-dataset"
    elif mutation == "overcoverage":
        description["embedding_status"]["covered_rows"] = 13
    with pytest.raises(
        ValueError,
        match=message,
    ):
        validate_description(description)


def test_not_reported_cannot_claim_a_coverage_value(description):
    # Zero is a known count, so it cannot accompany an unknown status.
    description["embedding_status"]["covered_rows"] = 0
    with pytest.raises(
        ValueError,
        match="leave optional fields null",
    ):
        validate_description(description)


@pytest.mark.parametrize(
    "count",
    [0, 12],
)
def test_empty_or_entirely_undated_datasets_have_no_invented_months(count):
    # Remove usable dates and confirm that no calendar month is invented.
    example = examples["example_a"]()
    example["records"] = example["records"][:count]
    for row in example["records"]:
        row["record_date"] = None
    description = description_for(example)
    assert description["monthly_counts"] == []
    assert description["dataset"]["record_start"] is None
    assert description["dataset"]["record_end"] is None
    validate_description(description)

    # A fabricated date bound must fail even when all counts still agree.
    description["dataset"]["record_start"] = "2026-10-18T00:00:00Z"
    with pytest.raises(
        ValueError,
        match="null date bounds",
    ):
        validate_description(description)


def test_explicit_unknown_platform_remains_in_totals_and_emits_a_warning(description):
    # Keep these records in the total while exposing their missing platform.
    description["sources"][0]["platform"] = "unknown"
    assert "platform_unknown" in warning_codes(validate_description(description))
    assert sum(row["record_count"] for row in description["sources"]) == 12


def test_timestamp_offsets_are_normalized_without_mutating_the_description(description):
    # An offset can move an instant into the previous month when expressed in UTC.
    assert utc_datetime(
        "2026-08-01T00:30:00+02:00",
        "record_date",
    ) == datetime(
        2026,
        7,
        31,
        22,
        30,
        tzinfo=UTC,
    )
    before = deepcopy(description)
    assert validate_description(description) == validate_description(description)
    assert description == before


def test_publication_identity_changes_independently_of_dataset_and_schema(description):
    # Two packages can publish the same dataset using the same storage schema.
    first, second = publication_for(description), publication_for(description)
    assert first["publication_id"] != second["publication_id"]
    assert first["dataset_id"] == second["dataset_id"]
    assert first["schema_version"] == second["schema_version"] == "voc-data-state-v1"
    assert QUERY_SCHEMA_VERSION == "voc-data-query-v1"
    validate_publication(
        first,
        description["dataset"]["dataset_id"],
    )
    validate_publication(
        second,
        description["dataset"]["dataset_id"],
    )


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("publication_id", "latest", "canonical UUID"),
        ("dataset_id", "other-dataset", "another dataset"),
        ("schema_version", "1.5.6", "Unsupported"),
        ("created_at", "2026-10-18", "timezone"),
    ],
)
def test_invalid_publication_identity_or_version(description, field, value, message):
    # Validate package metadata independently of the dataset description.
    publication = publication_for(description)
    publication[field] = value
    with pytest.raises(
        ValueError,
        match=message,
    ):
        validate_publication(
            publication,
            description["dataset"]["dataset_id"],
        )
