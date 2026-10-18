"""Lesson 04: immutable installation, atomic selection and reproducible queries."""

import hashlib
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from runpy import run_path
from uuid import uuid4

import duckdb
import pytest
from click.testing import CliRunner

from voc.cli import main
from voc.publications import store
from voc.publications.builder import build_bundle
from voc.publications.queries import query
from voc.publications.store import (
    activate_bundle,
    current_publication,
    open_publication,
)

helpers = run_path(str(Path(__file__).with_name("test_publication_builder.py")))


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    # Build independent fixtures once; tests copy them before modifying bytes.
    root = tmp_path_factory.mktemp("publication-sources")
    return tuple(
        build_bundle(
            helpers["fixture_description"](name),
            root / name,
        )
        for name in ("example_a", "example_b")
    )


@pytest.fixture
def locations(tmp_path):
    return tmp_path / "catalog", tmp_path / "state"


@pytest.fixture
def active(bundles, locations):
    catalog, state = locations
    return activate_bundle(
        bundles[0],
        catalog,
        state,
    )


def package_bytes(path):
    # Compare complete files rather than only reported counts or manifest fields.
    return {
        name: (path / name).read_bytes()
        for name in ("publication.duckdb", "manifest.json")
    }


def copy_bundle(path, destination):
    shutil.copytree(
        path,
        destination,
    )
    return destination


def test_install_copy_is_independent_and_reactivation_is_idempotent(bundles, locations):
    catalog, state = locations
    before = package_bytes(bundles[0])
    selected = activate_bundle(
        bundles[0],
        catalog,
        state,
    )
    installed = catalog / selected["publication_id"]
    assert package_bytes(installed) == before == package_bytes(bundles[0])
    assert installed.name == store.validate_bundle(installed)["publication_id"]
    assert (
        selected["manifest_sha256"]
        == hashlib.sha256(before["manifest.json"]).hexdigest()
    )
    pointer_before = (state / "active.json").read_bytes()
    assert (
        activate_bundle(
            bundles[0],
            catalog,
            state,
        )
        == selected
    )
    assert (
        activate_bundle(
            installed,
            catalog,
            state,
        )
        == selected
    )
    assert (state / "active.json").read_bytes() == pointer_before
    assert [p.name for p in catalog.iterdir() if p.is_dir()] == [
        selected["publication_id"]
    ]
    assert not list(catalog.glob(".install-*"))


def test_selecting_b_preserves_queries_and_bytes_for_a(bundles, locations, active):
    catalog, state = locations
    identifier = active["publication_id"]
    before = query(
        catalog,
        identifier,
        "overview",
    )
    bytes_before = package_bytes(catalog / identifier)
    selected_b = activate_bundle(
        bundles[1],
        catalog,
        state,
    )
    assert current_publication(state) == selected_b
    assert selected_b["publication_id"] != identifier
    assert (
        query(
            catalog,
            selected_b["publication_id"],
            "overview",
        )["data"]["row_count"]
        == 13
    )
    assert (
        query(
            catalog,
            identifier,
            "overview",
        )
        == before
    )
    assert before["data"]["row_count"] == 12
    assert package_bytes(catalog / identifier) == bytes_before
    # Explicit rollback selects the old bytes without rebuilding either package.
    assert (
        activate_bundle(
            bundles[0],
            catalog,
            state,
        )["publication_id"]
        == identifier
    )
    assert (
        query(
            catalog,
            identifier,
            "overview",
        )
        == before
    )


def test_catalog_remains_usable_without_the_source_bundle(tmp_path, bundles, locations):
    source = copy_bundle(
        bundles[0],
        tmp_path / "source-copy",
    )
    selected = activate_bundle(
        source,
        *locations,
    )
    shutil.rmtree(source)
    assert (
        query(
            locations[0],
            selected["publication_id"],
            "overview",
        )["data"]["row_count"]
        == 12
    )


@pytest.mark.parametrize(
    "damage",
    ["database", "future-schema", "manifest-path", "extra-file"],
)
def test_invalid_activation_keeps_previous_selection(
    tmp_path, bundles, locations, active, damage
):
    catalog, state = locations
    pointer_before = (state / "active.json").read_bytes()
    source = copy_bundle(
        bundles[1],
        tmp_path / "damaged",
    )
    if damage == "database":
        with (source / "publication.duckdb").open("ab") as stream:
            stream.write(b"corrupt")
    elif damage == "extra-file":
        (source / "private.txt").write_text("not part of a publication")
    else:
        manifest = helpers["read_manifest"](source)
        if damage == "future-schema":
            manifest["schema_version"] = "voc-data-state-v999"
        else:
            manifest["database"]["path"] = "../outside.duckdb"
        helpers["write_manifest"](
            source,
            manifest,
        )
    with pytest.raises(ValueError):
        activate_bundle(
            source,
            catalog,
            state,
        )
    assert (state / "active.json").read_bytes() == pointer_before
    assert current_publication(state) == active
    assert (
        query(
            catalog,
            active["publication_id"],
            "overview",
        )["data"]["row_count"]
        == 12
    )


@pytest.mark.parametrize(
    "damage",
    [
        "copy-corruption",
        "copy-error",
        "pointer-write",
        "pointer-replace",
        "catalog-sync",
    ],
)
def test_failures_leave_old_pointer_and_allow_retry(
    bundles, locations, active, monkeypatch, damage
):
    catalog, state = locations
    pointer_before = (state / "active.json").read_bytes()
    with monkeypatch.context() as patch:
        if damage in ("copy-corruption", "copy-error"):
            original = store.shutil.copyfile

            def copy(source, target, **kwargs):
                result = original(
                    source,
                    target,
                    **kwargs,
                )
                if Path(target).name == "publication.duckdb":
                    if damage == "copy-error":
                        raise OSError("injected copy failure")
                    with Path(target).open("ab") as stream:
                        stream.write(b"bad copy")
                return result

            patch.setattr(
                store.shutil,
                "copyfile",
                copy,
            )
        elif damage == "pointer-write":

            def write(*args, **kwargs):
                raise OSError("injected write failure")

            patch.setattr(
                store.json,
                "dump",
                write,
            )
        elif damage == "pointer-replace":

            def replace(*args, **kwargs):
                raise OSError("injected replace failure")

            patch.setattr(
                Path,
                "replace",
                replace,
            )
        else:
            original = store._sync_directory

            def sync(path):
                if path == catalog:
                    raise OSError("injected catalog sync failure")
                return original(path)

            patch.setattr(
                store,
                "_sync_directory",
                sync,
            )
        with pytest.raises((ValueError, OSError)):
            activate_bundle(
                bundles[1],
                catalog,
                state,
            )
    assert (state / "active.json").read_bytes() == pointer_before
    assert current_publication(state) == active
    assert not list(catalog.glob(".install-*"))
    assert not list(state.glob(".active-*"))
    # A complete inactive copy is safe to reuse after a failed pointer update.
    selected = activate_bundle(
        bundles[1],
        catalog,
        state,
    )
    assert (
        query(
            catalog,
            selected["publication_id"],
            "overview",
        )["data"]["row_count"]
        == 13
    )


def test_pointer_replacement_never_exposes_partial_json(
    bundles, locations, active, monkeypatch
):
    catalog, state = locations
    original = Path.replace
    observations = []

    def replace(path, destination):
        if Path(destination) == state / "active.json":
            observations.append(current_publication(state))
            assert (
                json.loads(path.read_text())["publication_id"]
                != active["publication_id"]
            )
        result = original(
            path,
            destination,
        )
        if Path(destination) == state / "active.json":
            observations.append(current_publication(state))
        return result

    monkeypatch.setattr(
        Path,
        "replace",
        replace,
    )
    selected = activate_bundle(
        bundles[1],
        catalog,
        state,
    )
    assert observations == [active, selected]


@pytest.mark.parametrize(
    "change",
    ["manifest-bytes", "database-content"],
)
def test_an_installed_id_cannot_acquire_different_bytes(
    tmp_path, bundles, locations, active, change
):
    catalog, state = locations
    copy = copy_bundle(
        bundles[0 if change == "manifest-bytes" else 1],
        tmp_path / "collision",
    )
    manifest_path = copy / "manifest.json"
    if change == "manifest-bytes":
        manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    else:
        manifest = helpers["read_manifest"](copy)
        manifest["publication_id"] = active["publication_id"]
        with duckdb.connect(str(copy / "publication.duckdb")) as db:
            db.execute(
                "UPDATE publication SET publication_id = ?",
                [active["publication_id"]],
            )
        helpers["write_manifest"](
            copy,
            manifest,
        )
        helpers["rehash"](copy)
    store.validate_bundle(copy)
    before = package_bytes(catalog / active["publication_id"])
    with pytest.raises(
        ValueError,
        match="different bytes",
    ):
        activate_bundle(
            copy,
            catalog,
            state,
        )
    assert current_publication(state) == active
    assert package_bytes(catalog / active["publication_id"]) == before


@pytest.mark.parametrize(
    "lock_root",
    ["catalog", "state"],
)
def test_other_process_cannot_activate_a_shared_resource(
    bundles, locations, active, tmp_path, lock_root
):
    catalog, state = locations
    root = catalog if lock_root == "catalog" else state
    script = "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read()"
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(root / ".publication-activation.lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        other_catalog = (
            catalog if lock_root == "catalog" else tmp_path / "other-catalog"
        )
        other_state = state if lock_root == "state" else tmp_path / "other-state"
        with pytest.raises(
            ValueError,
            match="activation is in progress",
        ):
            activate_bundle(
                bundles[1],
                other_catalog,
                other_state,
            )
        # Readers never acquire the write lock and remain available.
        assert current_publication(state) == active
        assert (
            query(
                catalog,
                active["publication_id"],
                "overview",
            )["data"]["row_count"]
            == 12
        )
    finally:
        process.communicate(timeout=5)
    assert (
        activate_bundle(
            bundles[1],
            catalog,
            state,
        )["publication_id"]
        != active["publication_id"]
    )


@pytest.mark.parametrize(
    "relationship",
    ["same", "nested-state", "nested-catalog", "inside-source"],
)
def test_catalog_and_state_cannot_overlap(bundles, tmp_path, relationship):
    catalog, state = tmp_path / "catalog", tmp_path / "state"
    if relationship == "same":
        state = catalog
    elif relationship == "nested-state":
        state = catalog / "state"
    elif relationship == "nested-catalog":
        catalog = state / "catalog"
    else:
        catalog = bundles[0] / "catalog"
    with pytest.raises(
        ValueError,
        match="directories|inside a source",
    ):
        activate_bundle(
            bundles[0],
            catalog,
            state,
        )
    assert not catalog.exists()
    assert not state.exists()


@pytest.mark.parametrize(
    "identifier",
    [
        "latest",
        "../outside",
        "/tmp/outside",
        "",
        "123",
        "3C9C5A39-19D8-41D2-AECF-E0585559101F",
        None,
    ],
)
def test_invalid_id_never_creates_storage(tmp_path, identifier):
    catalog = tmp_path / "missing"
    with pytest.raises(
        ValueError,
        match="canonical UUID",
    ):
        query(
            catalog,
            identifier,
            "overview",
        )
    assert not catalog.exists()


def test_missing_selection_and_missing_id_do_not_create_storage(tmp_path):
    with pytest.raises(
        LookupError,
        match="No active publication",
    ):
        current_publication(tmp_path / "state")
    with pytest.raises(
        LookupError,
        match="not installed",
    ):
        query(
            tmp_path / "catalog",
            str(uuid4()),
            "overview",
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("publication_id", "latest"),
        ("manifest_sha256", "wrong"),
        ("activated_at", "2026-10-18"),
        ("unexpected", "private-path"),
    ],
)
def test_invalid_pointer_is_rejected_without_repair(locations, active, field, value):
    _, state = locations
    pointer = dict(active)
    pointer[field] = value
    path = state / "active.json"
    path.write_text(json.dumps(pointer))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        current_publication(state)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "payload",
    [b'{"publication_id":1,"publication_id":2}', b"{", b" " * (64 * 1024 + 1)],
)
def test_unreadable_pointer_is_rejected(locations, active, payload):
    path = locations[1] / "active.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        current_publication(locations[1])
    assert path.read_bytes() == payload


def test_dangling_selection_symlink_is_not_treated_as_missing(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "active.json").symlink_to(tmp_path / "absent")
    with pytest.raises(
        ValueError,
        match="symlink",
    ):
        current_publication(state)


def test_catalog_entry_cannot_be_a_symlink_or_another_identity(
    bundles, locations, active, tmp_path
):
    catalog, _ = locations
    other_id = str(uuid4())
    (catalog / other_id).symlink_to(
        bundles[0],
        target_is_directory=True,
    )
    with pytest.raises(
        ValueError,
        match="symlink",
    ):
        query(
            catalog,
            other_id,
            "overview",
        )
    (catalog / other_id).unlink()
    copy_bundle(
        bundles[0],
        catalog / other_id,
    )
    with pytest.raises(
        ValueError,
        match="identity differ",
    ):
        query(
            catalog,
            other_id,
            "overview",
        )


def test_active_digest_is_checked_when_opening_the_selection(locations, active):
    catalog, _ = locations
    with open_publication(
        catalog,
        active["publication_id"],
        expected_manifest_sha256=active["manifest_sha256"],
    ) as opened:
        assert opened.manifest["publication_id"] == active["publication_id"]
    with (
        pytest.raises(
            ValueError,
            match="differs from the active",
        ),
        open_publication(
            catalog,
            active["publication_id"],
            expected_manifest_sha256="0" * 64,
        ),
    ):
        pytest.fail("A mismatching selection must not yield a connection")


def test_connection_is_read_only_restricts_external_access_and_closes(
    locations, active, tmp_path
):
    catalog, _ = locations
    installed = catalog / active["publication_id"]
    before = package_bytes(installed)
    private = tmp_path / "private.csv"
    private.write_text("secret\nprivate-value\n")
    with open_publication(
        catalog,
        active["publication_id"],
    ) as opened:
        connection = opened.connection
        with pytest.raises(duckdb.Error):
            connection.execute("UPDATE dataset SET row_count=0")
        with pytest.raises(duckdb.Error):
            connection.execute(
                "SELECT * FROM read_csv(?)",
                [str(private)],
            )
    with pytest.raises(duckdb.Error):
        connection.execute("SELECT 1")
    assert package_bytes(installed) == before


def test_reader_closes_when_the_consumer_raises(locations, active):
    with (
        pytest.raises(
            RuntimeError,
            match="consumer failure",
        ),
        open_publication(
            locations[0],
            active["publication_id"],
        ) as opened,
    ):
        connection = opened.connection
        raise RuntimeError("consumer failure")
    with pytest.raises(duckdb.Error):
        connection.execute("SELECT 1")


@pytest.mark.parametrize(
    "kind",
    ["topics", "SELECT * FROM dataset", "../sources", "", None],
)
def test_unsupported_capability_fails_instead_of_returning_empty_data(tmp_path, kind):
    with pytest.raises(
        ValueError,
        match="Capability not available",
    ):
        query(
            tmp_path / "absent",
            str(uuid4()),
            kind,
        )
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "kind",
    ["overview", "sources", "coverage", "freshness", "embeddings"],
)
def test_query_envelopes_have_stable_verifiable_ids(locations, active, kind):
    catalog, _ = locations
    first = query(
        catalog,
        active["publication_id"],
        kind,
    )
    assert first == query(
        catalog,
        active["publication_id"],
        kind,
    )
    assert set(first) == {
        "schema_version",
        "publication_id",
        "kind",
        "result_id",
        "data",
        "warnings",
    }
    assert first["schema_version"] == "voc-data-query-v1"
    body = {key: value for key, value in first.items() if key != "result_id"}
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == first["result_id"]
    assert {w["code"] for w in first["warnings"]} == {
        "record_dates_missing",
        "collection_date_unknown",
        "embeddings_not_reported",
    }
    assert "/tmp/" not in json.dumps(first)


def test_queries_preserve_coverage_and_distinct_date_meanings(locations, active):
    catalog, _ = locations
    identifier = active["publication_id"]
    sources = query(
        catalog,
        identifier,
        "sources",
    )["data"]
    assert [(s["platform"], s["record_count"]) for s in sources] == [
        ("app_store", 4),
        ("google_play", 8),
    ]
    coverage = query(
        catalog,
        identifier,
        "coverage",
    )["data"]
    assert coverage == {
        "months": [
            {"month": "2026-07", "record_count": 4},
            {"month": "2026-08", "record_count": 4},
            {"month": "2026-09", "record_count": 3},
        ],
        "unknown_dates": 1,
    }
    freshness = query(
        catalog,
        identifier,
        "freshness",
    )["data"]
    assert freshness["prepared_at"] is None
    assert freshness["publication_created_at"].endswith("Z")
    assert freshness["sources"][0]["latest_collection_at"] is None
    assert freshness["sources"][1]["latest_collection_at"] == "2026-10-15T12:00:00Z"
    assert (
        freshness["sources"][1]["collection_evidence_id"]
        == freshness["evidence"][0]["evidence_id"]
    )
    embeddings = query(
        catalog,
        identifier,
        "embeddings",
    )["data"]
    assert embeddings["state"] == "not_reported"
    assert embeddings["covered_rows"] is None
    assert embeddings["evidence"] == []


@pytest.mark.parametrize(
    "verified",
    [False, True],
)
def test_embedding_query_carries_partial_coverage_and_evidence(
    tmp_path, locations, verified
):
    description = helpers["fixture_description"]()
    dataset_id = description["dataset"]["dataset_id"]
    description["embedding_status"].update(
        {
            "state": "verified" if verified else "registered",
            "release_id": "embedding-example",
            "model": "example-model",
            "covered_rows": 6,
            "verification_evidence_id": "verification" if verified else None,
        }
    )
    description["provenance"].append(
        {
            "evidence_id": "registration",
            "dataset_id": dataset_id,
            "kind": "embedding_registration",
            "evidence_ref": "embedding-release:embedding-example",
            "description": "Fictional association.",
        }
    )
    if verified:
        description["provenance"].append(
            {
                "evidence_id": "verification",
                "dataset_id": dataset_id,
                "kind": "embedding_verification",
                "evidence_ref": "embedding-release:embedding-example",
                "description": "Fictional verification.",
            }
        )
    bundle = build_bundle(
        description,
        tmp_path / "with-embeddings",
    )
    selected = activate_bundle(
        bundle,
        *locations,
    )
    result = query(
        locations[0],
        selected["publication_id"],
        "embeddings",
    )
    assert result["data"]["covered_rows"] == 6
    assert len(result["data"]["evidence"]) == (2 if verified else 1)
    codes = {w["code"] for w in result["warnings"]}
    assert "embeddings_partial_coverage" in codes
    assert ("embeddings_not_verified" in codes) is not verified


def test_corrupted_installed_data_is_rejected_on_every_read(locations, active):
    catalog, _ = locations
    path = catalog / active["publication_id"] / "publication.duckdb"
    with path.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(
        ValueError,
        match="checksum",
    ):
        query(
            catalog,
            active["publication_id"],
            "overview",
        )


def test_cli_activate_current_query_share_the_library_results(bundles, locations):
    catalog, state = locations
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "publication",
            "activate",
            "--bundle",
            str(bundles[0]),
            "--catalog",
            str(catalog),
            "--state",
            str(state),
        ],
    )
    assert result.exit_code == 0, result.output
    selected = json.loads(result.stdout)
    result = runner.invoke(
        main,
        ["publication", "current", "--state", str(state)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == selected
    result = runner.invoke(
        main,
        [
            "publication",
            "query",
            "--catalog",
            str(catalog),
            "--publication-id",
            selected["publication_id"],
            "--kind",
            "sources",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == query(
        catalog,
        selected["publication_id"],
        "sources",
    )


def test_cli_read_errors_are_visible_without_creating_data(tmp_path):
    runner = CliRunner()
    commands = [
        ["current", "--state", str(tmp_path / "state")],
        [
            "query",
            "--catalog",
            str(tmp_path / "catalog"),
            "--publication-id",
            str(uuid4()),
            "--kind",
            "overview",
        ],
        [
            "query",
            "--catalog",
            str(tmp_path / "catalog"),
            "--publication-id",
            "latest",
            "--kind",
            "sources",
        ],
        [
            "query",
            "--catalog",
            str(tmp_path / "catalog"),
            "--publication-id",
            str(uuid4()),
            "--kind",
            "topics",
        ],
    ]
    for command in commands:
        result = runner.invoke(
            main,
            ["publication", *command],
        )
        assert result.exit_code != 0
        assert "Traceback" not in result.output
    assert list(tmp_path.iterdir()) == []


def test_new_catalog_files_are_readable_without_granting_write_access(
    locations, active
):
    catalog, state = locations
    installed = catalog / active["publication_id"]
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755
    for path in (
        installed / "manifest.json",
        installed / "publication.duckdb",
        state / "active.json",
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_failure_after_pointer_replacement_can_be_resolved_by_readback(
    bundles, locations, active, monkeypatch
):
    catalog, state = locations
    original = store._sync_directory

    def fail_state_sync(path):
        if path == state:
            raise OSError("injected post-replacement sync failure")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(
            store,
            "_sync_directory",
            fail_state_sync,
        )
        with pytest.raises(
            OSError,
            match="post-replacement",
        ):
            activate_bundle(
                bundles[1],
                catalog,
                state,
            )
    # Replacement is the commit point; readback reveals the complete new selection.
    selected = current_publication(state)
    assert selected["publication_id"] != active["publication_id"]
    assert (
        query(
            catalog,
            selected["publication_id"],
            "overview",
        )["data"]["row_count"]
        == 13
    )
    assert (
        activate_bundle(
            bundles[1],
            catalog,
            state,
        )
        == selected
    )


@pytest.mark.parametrize(
    "case",
    ["empty", "undated", "gap"],
)
def test_coverage_keeps_missing_information_distinct_from_zero(
    tmp_path, locations, case
):
    from voc.publications.exporter import describe_dataset

    example = helpers["examples"]["example_a"]()
    snapshot = helpers["snapshot_for"](example)
    if case == "empty":
        snapshot.data = snapshot.data.iloc[:0].copy()
    elif case == "undated":
        snapshot.data["record_date"] = None
    else:
        snapshot.data = snapshot.data[
            ~snapshot.data["record_date"].fillna("").str.startswith("2026-08")
        ].copy()
    snapshot.info["row_count"] = len(snapshot.data)
    bundle = build_bundle(
        describe_dataset(snapshot),
        tmp_path / "special",
    )
    selected = activate_bundle(
        bundle,
        *locations,
    )
    result = query(
        locations[0],
        selected["publication_id"],
        "coverage",
    )
    if case == "gap":
        assert [row["month"] for row in result["data"]["months"]] == [
            "2026-07",
            "2026-09",
        ]
        assert result["data"]["unknown_dates"] == 1
    else:
        assert result["data"]["months"] == []
        assert result["data"]["unknown_dates"] == (12 if case == "undated" else 0)


def test_result_identity_does_not_depend_on_catalog_location(
    bundles, locations, active, tmp_path
):
    selected = activate_bundle(
        bundles[0],
        tmp_path / "other-catalog",
        tmp_path / "other-state",
    )
    assert selected["publication_id"] == active["publication_id"]
    for kind in ("overview", "sources", "coverage", "freshness", "embeddings"):
        assert query(
            locations[0],
            active["publication_id"],
            kind,
        ) == query(
            tmp_path / "other-catalog",
            selected["publication_id"],
            kind,
        )
