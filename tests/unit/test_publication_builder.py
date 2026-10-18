"""Lesson 03: export, publish and validate real files without network or ML."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from runpy import run_path
from uuid import uuid4

import duckdb
import pandas as pd
import pytest
from click.testing import CliRunner

from voc.cli import main
from voc.models import Snapshot
from voc.publications import builder
from voc.publications.contracts import validate_description
from voc.publications.exporter import describe_dataset
from voc.publications.store import file_sha256, validate_bundle

examples = run_path(str(Path(__file__).parents[1] / "fixtures/data_state/example.py"))
DATASET_VERSION = "3c9c5a39-19d8-41d2-aecf-e0585559101f"


def snapshot_for(example, source="fixture"):
    # Preserve reader metadata shape, including fields that must never be exported.
    info = {
        "source": source,
        "dataset_id": example["dataset_id"],
        "source_version": example["source_version"],
        "artifact_id": DATASET_VERSION,
        "release_id": DATASET_VERSION,
        "row_count": len(example["records"]),
        "stage": "prepared",
        "schema_version": "workable-v1",
        "created_at": "2026-10-17T12:00:00Z",
        "private_path": "/private/operator/secret",
        "credentials": "DO-NOT-PUBLISH",
    }
    return Snapshot(
        pd.DataFrame(example["records"]),
        info,
    )


def fixture_description(name="example_a"):
    # Exercise the exporter for counts; collection evidence is explicitly fictional.
    example = examples[name]()
    description = describe_dataset(snapshot_for(example))
    description["provenance"] = deepcopy(example["provenance"])
    for source in description["sources"]:
        source.update(example["collection"][source["platform"]])
    validate_description(description)
    return description


@pytest.fixture
def description():
    return fixture_description()


@pytest.fixture
def bundle(tmp_path, description):
    return builder.build_bundle(
        description,
        tmp_path / "a",
    )


def read_manifest(path):
    return json.loads((path / "manifest.json").read_text())


def write_manifest(path, manifest):
    (path / "manifest.json").write_text(json.dumps(manifest))


def rehash(path):
    # Simulate a deliberate database rewrite with an internally consistent checksum.
    manifest = read_manifest(path)
    database = path / "publication.duckdb"
    manifest["database"]["size_bytes"] = database.stat().st_size
    manifest["database"]["sha256"] = file_sha256(database)
    write_manifest(
        path,
        manifest,
    )


@pytest.mark.parametrize(
    "name,total,apple,months",
    [("example_a", 12, 4, [4, 4, 3]), ("example_b", 13, 5, [4, 4, 4])],
)
def test_builds_complete_fixture_packages(tmp_path, name, total, apple, months):
    # Read independently to check counts, metadata, integrity and closed-file output.
    description = fixture_description(name)
    before = deepcopy(description)
    path = builder.build_bundle(
        description,
        tmp_path / name,
    )
    assert {p.name for p in path.iterdir()} == {"manifest.json", "publication.duckdb"}
    manifest = validate_bundle(path)
    database = path / "publication.duckdb"
    assert (
        hashlib.sha256(database.read_bytes()).hexdigest()
        == manifest["database"]["sha256"]
    )
    assert database.stat().st_size == manifest["database"]["size_bytes"]
    assert manifest["producer"]["duckdb_version"] == duckdb.__version__
    assert manifest["schema_version"] == "voc-data-state-v1"
    assert description == before
    with duckdb.connect(
        str(database),
        read_only=True,
    ) as db:
        assert db.execute(
            "SELECT row_count, unknown_dates FROM dataset"
        ).fetchone() == (total, 1)
        assert db.execute(
            "SELECT platform, record_count FROM sources ORDER BY platform"
        ).fetchall() == [("app_store", apple), ("google_play", 8)]
        assert [
            row[0]
            for row in db.execute(
                "SELECT record_count FROM monthly_counts ORDER BY month"
            ).fetchall()
        ] == months
        assert db.execute(
            "SELECT collection_state FROM sources WHERE platform='app_store'"
        ).fetchone() == ("unknown",)
        assert db.execute(
            "SELECT collection_state FROM sources WHERE platform='google_play'"
        ).fetchone() == ("verified",)
    assert not list(tmp_path.glob(f".{name}-*/"))


def test_new_publications_retain_dataset_identity(tmp_path, description):
    first = validate_bundle(
        builder.build_bundle(
            description,
            tmp_path / "first",
        )
    )
    second = validate_bundle(
        builder.build_bundle(
            description,
            tmp_path / "second",
        )
    )
    assert first["publication_id"] != second["publication_id"]
    assert first["dataset_id"] == second["dataset_id"]
    assert first["schema_version"] == second["schema_version"]


@pytest.mark.parametrize(
    "source",
    ["s3", "personal"],
)
def test_real_source_mapping_is_stable_and_excludes_private_fields(source):
    snapshot = snapshot_for(
        examples["example_a"](),
        source,
    )
    frame_before, info_before = snapshot.data.copy(deep=True), deepcopy(snapshot.info)
    first = describe_dataset(snapshot)
    assert first == describe_dataset(snapshot)
    assert first["dataset"]["dataset_id"] == f"{source}-{DATASET_VERSION}"
    assert first["dataset"]["source_version"] == DATASET_VERSION
    assert first["dataset"]["prepared_at"] is None
    assert all(s["collection_state"] == "unknown" for s in first["sources"])
    assert first["embedding_status"]["state"] == "not_reported"
    encoded = json.dumps(
        first,
        default=str,
    )
    for private in ("/private/", "DO-NOT-PUBLISH", "created_at", "credentials"):
        assert private not in encoded
    pd.testing.assert_frame_equal(
        snapshot.data,
        frame_before,
    )
    assert snapshot.info == info_before


def test_personal_catalog_without_source_field():
    snapshot = snapshot_for(
        examples["example_a"](),
        "personal",
    )
    del snapshot.info["source"]
    assert (
        describe_dataset(snapshot)["dataset"]["dataset_id"]
        == f"personal-{DATASET_VERSION}"
    )


@pytest.mark.parametrize(
    "rows",
    [[], [None, None], ["bad-date", "2026-08-01T00:30:00+02:00"]],
)
def test_empty_invalid_dates_and_utc_boundaries_are_preserved(rows):
    snapshot = snapshot_for(examples["example_a"]())
    snapshot.data = pd.DataFrame({"platform": [None] * len(rows), "record_date": rows})
    snapshot.info["row_count"] = len(rows)
    result = describe_dataset(snapshot)
    assert result["dataset"]["row_count"] == len(rows)
    if rows and rows[0] == "bad-date":
        assert result["dataset"]["unknown_dates"] == 1
        assert result["monthly_counts"] == [
            {
                "dataset_id": snapshot.info["dataset_id"],
                "month": "2026-07",
                "record_count": 1,
            }
        ]
    else:
        assert result["dataset"]["unknown_dates"] == len(rows)
        assert result["dataset"]["record_start"] is None
        assert result["monthly_counts"] == []
    if rows:
        assert result["sources"][0]["platform"] == "unknown"
        assert result["sources"][0]["record_count"] == len(rows)
    validate_description(result)


def test_missing_platforms_do_not_disappear():
    snapshot = snapshot_for(examples["example_a"]())
    snapshot.data.loc[:2, "platform"] = [None, "", "  "]
    result = describe_dataset(snapshot)
    assert sum(s["record_count"] for s in result["sources"]) == 12
    assert (
        next(s["record_count"] for s in result["sources"] if s["platform"] == "unknown")
        == 3
    )
    assert "platform_unknown" in {w["code"] for w in validate_description(result)}


@pytest.mark.parametrize(
    "field,value",
    [
        ("row_count", 11),
        ("row_count", True),
        ("row_count", "12"),
        ("stage", "raw"),
        ("schema_version", "unknown-schema"),
        ("source", "unknown"),
        ("release_id", "latest"),
        ("prepared_at", "2026-10-18"),
    ],
)
def test_inconsistent_metadata_is_rejected(field, value):
    snapshot = snapshot_for(
        examples["example_a"](),
        "s3",
    )
    snapshot.info[field] = value
    with pytest.raises(ValueError):
        describe_dataset(snapshot)


def test_explicit_prepared_at_is_normalized_without_becoming_collection_time():
    snapshot = snapshot_for(
        examples["example_a"](),
        "s3",
    )
    snapshot.info["prepared_at"] = "2026-10-18T01:00:00+02:00"
    result = describe_dataset(snapshot)
    assert result["dataset"]["prepared_at"].isoformat() == "2026-10-17T23:00:00+00:00"
    assert all(s["latest_collection_at"] is None for s in result["sources"])


@pytest.mark.parametrize(
    "column",
    ["platform", "record_date"],
)
def test_missing_columns_fail_clearly(column):
    snapshot = snapshot_for(examples["example_a"]())
    snapshot.data = snapshot.data.drop(columns=[column])
    with pytest.raises(
        ValueError,
        match="columns",
    ):
        describe_dataset(snapshot)


@pytest.mark.parametrize(
    "entry",
    ["directory", "file", "dangling-symlink"],
)
def test_existing_destinations_are_never_replaced(tmp_path, description, entry):
    destination = tmp_path / "existing"
    if entry == "directory":
        destination.mkdir()
    elif entry == "file":
        destination.write_text("keep")
    else:
        destination.symlink_to(tmp_path / "missing")
    with pytest.raises(FileExistsError):
        builder.build_bundle(
            description,
            destination,
        )
    if entry == "file":
        assert destination.read_text() == "keep"
    elif entry == "directory":
        assert list(destination.iterdir()) == []
    else:
        assert destination.is_symlink()


def test_invalid_description_leaves_no_output_directories(tmp_path, description):
    description["dataset"]["row_count"] = 99
    with pytest.raises(ValueError):
        builder.build_bundle(
            description,
            tmp_path / "missing" / "final",
        )
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize(
    "phase",
    ["persisted-validation", "manifest-write", "bundle-validation", "rename"],
)
def test_failed_build_is_not_visible_and_staging_is_removed(
    tmp_path, description, monkeypatch, phase
):
    destination = tmp_path / "final"

    def fail(*args, **kwargs):
        assert not destination.exists()
        raise OSError("simulated failure")

    if phase == "persisted-validation":
        monkeypatch.setattr(
            builder,
            "validate_database",
            fail,
        )
    elif phase == "manifest-write":
        monkeypatch.setattr(
            builder.json,
            "dump",
            fail,
        )
    elif phase == "bundle-validation":
        monkeypatch.setattr(
            builder,
            "validate_bundle",
            fail,
        )
    else:
        monkeypatch.setattr(
            Path,
            "rename",
            fail,
        )
    with pytest.raises(
        OSError,
        match="simulated",
    ):
        builder.build_bundle(
            description,
            destination,
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".final-*/"))


def test_destination_appearing_during_build_is_preserved(
    tmp_path, description, monkeypatch
):
    destination = tmp_path / "final"
    original = builder.validate_bundle

    def validate_then_create(path):
        result = original(path)
        destination.mkdir()
        (destination / "keep").write_text("external")
        return result

    monkeypatch.setattr(
        builder,
        "validate_bundle",
        validate_then_create,
    )
    with pytest.raises(FileExistsError):
        builder.build_bundle(
            description,
            destination,
        )
    assert (destination / "keep").read_text() == "external"


def test_process_lock_prevents_competing_writers(tmp_path, description):
    # Hold the same OS lock from another process, then verify it releases cleanly.
    script = "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read()"
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path / ".publication-build.lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(
            ValueError,
            match="Another publication build",
        ):
            builder.build_bundle(
                description,
                tmp_path / "a",
            )
        assert not (tmp_path / "a").exists()
    finally:
        process.communicate(timeout=5)
    validate_bundle(
        builder.build_bundle(
            description,
            tmp_path / "a",
        )
    )


def test_byte_tampering_fails_before_database_open(bundle, monkeypatch):
    path = bundle / "publication.duckdb"
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)

    def must_not_open(*args, **kwargs):
        pytest.fail("Integrity must be checked before opening DuckDB")

    monkeypatch.setattr(
        duckdb,
        "connect",
        must_not_open,
    )
    with pytest.raises(
        ValueError,
        match="checksum",
    ):
        validate_bundle(bundle)


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", 2),
        ("format_version", True),
        ("schema_version", "unsupported"),
        ("capabilities", ["execute_sql"]),
        ("publication_id", "not-an-id"),
        ("dataset_id", "another-dataset"),
        ("created_at", "2026-01-01T00:00:00Z"),
    ],
)
def test_invalid_manifest_is_rejected(bundle, field, value):
    manifest = read_manifest(bundle)
    manifest[field] = value
    write_manifest(
        bundle,
        manifest,
    )
    with pytest.raises(ValueError):
        validate_bundle(bundle)


@pytest.mark.parametrize(
    "path",
    ["../outside.duckdb", "/tmp/outside.duckdb", "https://example.com/db"],
)
def test_manifest_cannot_select_a_different_database(bundle, path):
    manifest = read_manifest(bundle)
    manifest["database"]["path"] = path
    write_manifest(
        bundle,
        manifest,
    )
    with pytest.raises(
        ValueError,
        match="Database path",
    ):
        validate_bundle(bundle)


@pytest.mark.parametrize(
    "change",
    ["extra-file", "symlink", "oversized-json", "duplicate-field"],
)
def test_invalid_package_shape_is_rejected(bundle, tmp_path, change):
    path = bundle / "manifest.json"
    if change == "extra-file":
        (bundle / "publication.duckdb.wal").write_text("unfinished")
    elif change == "symlink":
        target = tmp_path / "outside.json"
        path.rename(target)
        path.symlink_to(target)
    elif change == "oversized-json":
        path.write_text(" " * (64 * 1024 + 1))
    else:
        path.write_text('{"format_version":1,' + path.read_text()[1:])
    with pytest.raises(ValueError):
        validate_bundle(bundle)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE sources SET record_count=7 WHERE platform='google_play'",
        "ALTER TABLE dataset ADD COLUMN private_data VARCHAR",
        "CREATE TABLE extra_data (secret VARCHAR)",
        "CREATE MACRO surprise() AS 1",
        "ALTER TABLE monthly_counts RENAME TO hidden; CREATE VIEW monthly_counts AS SELECT * FROM hidden",
    ],
)
def test_valid_checksum_cannot_hide_bad_rows_or_schema(bundle, sql):
    with duckdb.connect(str(bundle / "publication.duckdb")) as db:
        db.execute(sql)
    rehash(bundle)
    with pytest.raises(ValueError):
        validate_bundle(bundle)


def test_validation_is_read_only(bundle):
    before = {p.name: p.read_bytes() for p in bundle.iterdir()}
    validate_bundle(bundle)
    assert before == {p.name: p.read_bytes() for p in bundle.iterdir()}


@pytest.mark.parametrize(
    "source",
    ["s3", "personal"],
)
def test_cli_build_uses_existing_reader_and_returns_clean_json(
    tmp_path, monkeypatch, source
):
    import voc.datasets

    calls = []

    def read(version, *, source):
        calls.append((version, source))
        print("reader diagnostic")
        return snapshot_for(
            examples["example_a"](),
            source,
        )

    monkeypatch.setattr(
        voc.datasets,
        "get_dataset",
        read,
    )
    destination = tmp_path / source
    result = CliRunner().invoke(
        main,
        [
            "publication",
            "build",
            "--source",
            source,
            "--dataset-version",
            DATASET_VERSION,
            "--output",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["dataset_id"] == f"{source}-{DATASET_VERSION}"
    assert output["destination"] == str(destination)
    assert output["warnings"]
    assert "reader diagnostic" in result.stderr
    assert calls == [(DATASET_VERSION, source)]
    validate_bundle(destination)


@pytest.mark.parametrize(
    "version",
    ["latest", "../private", "", "ABC"],
)
def test_cli_rejects_unpinned_identity_before_reading(tmp_path, monkeypatch, version):
    import voc.datasets

    def must_not_read(*args, **kwargs):
        pytest.fail("Invalid input must not read a dataset")

    monkeypatch.setattr(
        voc.datasets,
        "get_dataset",
        must_not_read,
    )
    result = CliRunner().invoke(
        main,
        [
            "publication",
            "build",
            "--source",
            "s3",
            "--dataset-version",
            version,
            "--output",
            str(tmp_path / "a"),
        ],
    )
    assert result.exit_code != 0
    assert not (tmp_path / "a").exists()


def test_cli_checks_loaded_identity(tmp_path, monkeypatch):
    import voc.datasets

    snapshot = snapshot_for(
        examples["example_a"](),
        "s3",
    )
    snapshot.info["release_id"] = str(uuid4())
    monkeypatch.setattr(
        voc.datasets,
        "get_dataset",
        lambda *args, **kwargs: snapshot,
    )
    result = CliRunner().invoke(
        main,
        [
            "publication",
            "build",
            "--source",
            "s3",
            "--dataset-version",
            DATASET_VERSION,
            "--output",
            str(tmp_path / "a"),
        ],
    )
    assert result.exit_code != 0
    assert "identity differs" in result.output
    assert not (tmp_path / "a").exists()


def test_other_cli_commands_do_not_require_duckdb():
    script = """
import builtins
original = builtins.__import__
def without_duckdb(name, *args, **kwargs):
    if name == 'duckdb':
        raise ImportError('not installed')
    return original(name, *args, **kwargs)
builtins.__import__ = without_duckdb
from voc.cli import main
from click.testing import CliRunner
for command in (['--help'], ['version'], ['publication', 'build', '--help']):
    result = CliRunner().invoke(main, command)
    assert result.exit_code == 0, result.output
result = CliRunner().invoke(main, ['publication', 'build', '--source', 's3', '--dataset-version', '3c9c5a39-19d8-41d2-aecf-e0585559101f', '--output', 'unused-publication-output'])
assert result.exit_code != 0
assert 'voc[publications]' in result.output
"""
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
    )


@pytest.mark.parametrize(
    "destination",
    ["s3://bucket/package", Path("s3://bucket/package")],
)
def test_remote_output_is_not_misinterpreted_as_local(description, destination):
    with pytest.raises(
        ValueError,
        match="local directory",
    ):
        builder.build_bundle(
            description,
            destination,
        )


def test_cli_personal_build_reads_the_existing_catalog(tmp_path, monkeypatch):
    from voc.datasets import publish
    from voc.datasets.zenml_reader import ZenMLReader

    # The real catalog and resolver run; only optional ZenML materialization is replaced.
    monkeypatch.setenv(
        "VOC_DATA_DIR",
        str(tmp_path / "runtime"),
    )
    snapshot = snapshot_for(
        examples["example_a"](),
        "personal",
    )
    info = {
        "artifact_id": DATASET_VERSION,
        "run_id": str(uuid4()),
        "raw_revision_id": str(uuid4()),
        "schema_version": "workable-v1",
        "row_count": 12,
    }
    publish(info)

    def load_artifact(self, artifact_id):
        assert artifact_id == DATASET_VERSION
        return snapshot.data

    monkeypatch.setattr(
        ZenMLReader,
        "read",
        load_artifact,
    )
    result = CliRunner().invoke(
        main,
        [
            "publication",
            "build",
            "--source",
            "personal",
            "--dataset-version",
            DATASET_VERSION,
            "--output",
            str(tmp_path / "bundle"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dataset_id"] == f"personal-{DATASET_VERSION}"
    validate_bundle(tmp_path / "bundle")
