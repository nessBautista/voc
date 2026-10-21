"""HTTP behavior for pinned queries, activation changes and unavailable data."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import duckdb
import pytest
from fastapi.testclient import TestClient
from voc_app import data_api
from voc_app.main import create_app

from voc.publications import store
from voc.publications.queries import query


def error_code(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert set(body) == {"error"}
    assert response.headers["cache-control"] == "no-store"
    assert "Traceback" not in response.text


def file_bytes(root):
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def test_liveness_without_a_publication_does_not_create_catalog(client, settings):
    assert client.get("/health/live").json() == {"status": "alive"}
    for path in ("/health/ready", "/api/v1/publications/current"):
        error_code(client.get(path), 503, "no_active_publication")
    assert not settings.publications_dir.exists()
    assert not (settings.state_dir / "active.json").exists()
    assert (settings.state_dir / "conversations.sqlite3").is_file()


def test_current_validates_selection_and_reports_capabilities(client, active):
    response = client.get("/api/v1/publications/current")
    assert response.status_code == 200
    result = response.json()
    assert {key: result[key] for key in active} == active
    assert result["dataset_id"] == "fixture-data-state-a"
    assert result["schema_version"] == "voc-data-state-v1"
    assert result["capabilities"] == list(store.CAPABILITIES)
    assert result["warnings"]
    assert response.headers["cache-control"] == "no-store"
    # Readiness no longer names the publication; that is signed-in information.
    assert client.get("/health/ready").json() == {"status": "ready"}


@pytest.mark.parametrize("kind", store.CAPABILITIES)
def test_api_returns_the_library_result_without_rewriting_evidence(
    client, settings, active, kind
):
    identifier = active["publication_id"]
    response = client.get(f"/api/v1/publications/{identifier}/{kind}")
    assert response.status_code == 200
    result = response.json()
    assert result == query(settings.publications_dir, identifier, kind)
    assert result == client.get(f"/api/v1/publications/{identifier}/{kind}").json()
    digest = result.pop("result_id")
    assert (
        digest
        == hashlib.sha256(
            json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    assert str(settings.publications_dir) not in response.text


def test_counts_missing_dates_and_unknown_metadata_remain_distinct(client, active):
    base = f"/api/v1/publications/{active['publication_id']}"
    assert client.get(base + "/overview").json()["data"]["row_count"] == 12
    assert [
        row["platform"] for row in client.get(base + "/sources").json()["data"]
    ] == [
        "app_store",
        "google_play",
    ]
    coverage = client.get(base + "/coverage").json()["data"]
    assert coverage == {
        "months": [
            {"month": "2026-07", "record_count": 4},
            {"month": "2026-08", "record_count": 4},
            {"month": "2026-09", "record_count": 3},
        ],
        "unknown_dates": 1,
    }
    freshness = client.get(base + "/freshness").json()["data"]
    assert freshness["prepared_at"] is None
    assert freshness["sources"][0]["latest_collection_at"] is None
    assert freshness["sources"][1]["latest_collection_at"] == "2026-10-15T12:00:00Z"
    assert client.get(base + "/embeddings").json()["data"]["state"] == "not_reported"


def test_activation_changes_current_but_preserves_old_queries(
    client, settings, bundles, active
):
    identifier = active["publication_id"]
    path = f"/api/v1/publications/{identifier}/overview"
    previous = client.get(path).json()
    selected = store.activate_bundle(
        bundles[1], settings.publications_dir, settings.state_dir
    )
    assert (
        client.get("/api/v1/publications/current").json()["publication_id"]
        == selected["publication_id"]
    )
    assert (
        client.get(
            f"/api/v1/publications/{selected['publication_id']}/overview"
        ).json()["data"]["row_count"]
        == 13
    )
    assert client.get(path).json() == previous


def test_inflight_current_is_consistent_during_activation(
    client, settings, bundles, active, monkeypatch
):
    original = store.current_publication

    def select_b_after_read(state):
        pointer = original(state)
        # Restore before activation, which itself reads the previous pointer.
        monkeypatch.setattr(store, "current_publication", original)
        store.activate_bundle(bundles[1], settings.publications_dir, settings.state_dir)
        return pointer

    monkeypatch.setattr(store, "current_publication", select_b_after_read)
    result = client.get("/api/v1/publications/current").json()
    assert result["publication_id"] == active["publication_id"]
    assert result["dataset_id"] == "fixture-data-state-a"
    assert (
        client.get("/api/v1/publications/current").json()["dataset_id"]
        == "fixture-data-state-b"
    )


def test_queries_survive_missing_selection_and_application_restart(settings, active):
    path = f"/api/v1/publications/{active['publication_id']}/overview"
    with TestClient(create_app(settings)) as first:
        before = first.get(path).json()
    (settings.state_dir / "active.json").unlink()
    with TestClient(create_app(settings)) as second:
        error_code(second.get("/health/ready"), 503, "no_active_publication")
        assert second.get(path).json() == before


def test_queries_leave_package_and_state_bytes_unchanged(client, settings, active):
    before = file_bytes(settings.publications_dir.parent)
    client.get("/health/ready")
    client.get("/api/v1/publications/current")
    for kind in store.CAPABILITIES:
        client.get(f"/api/v1/publications/{active['publication_id']}/{kind}")
    assert file_bytes(settings.publications_dir.parent) == before


def test_concurrent_reads_share_no_connection(client, active):
    path = f"/api/v1/publications/{active['publication_id']}/overview"
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.get(path), range(8)))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["result_id"] for response in responses}) == 1


@pytest.mark.parametrize(
    "identifier",
    [
        "latest",
        "invalid",
        "B47AD1B3-70AC-485C-A25E-53A3D2FE488D",
        "00000000000000000000000000000000",
    ],
)
def test_bad_ids_are_not_filesystem_paths(client, settings, identifier):
    error_code(
        client.get(f"/api/v1/publications/{identifier}/overview"),
        422,
        "invalid_publication_id",
    )
    assert not settings.publications_dir.exists()


def test_unknown_id_is_not_an_empty_dataset(client):
    error_code(
        client.get(f"/api/v1/publications/{uuid4()}/overview"),
        404,
        "publication_not_found",
    )


@pytest.mark.parametrize("kind", ["topics", "sentiment", "SELECT 1"])
def test_unsupported_query_is_rejected(client, active, kind):
    error_code(
        client.get(f"/api/v1/publications/{active['publication_id']}/{kind}"),
        422,
        "capability_unavailable",
    )


@pytest.mark.parametrize(
    "suffix",
    ["?platform=google_play", "?sql=SELECT+1", "?catalog=/private", "?limit=1"],
)
def test_unsupported_filters_are_not_silently_ignored(client, active, suffix):
    error_code(
        client.get(f"/api/v1/publications/{active['publication_id']}/overview{suffix}"),
        422,
        "invalid_request",
    )
    error_code(
        client.get("/api/v1/publications/current" + suffix), 422, "invalid_request"
    )


@pytest.mark.parametrize(
    "damage",
    [
        "pointer_json",
        "pointer_digest",
        "manifest_schema",
        "database",
        "missing",
        "symlink",
    ],
)
def test_damaged_active_package_fails_readiness(
    client, settings, active, damage, tmp_path
):
    pointer = settings.state_dir / "active.json"
    package = settings.publications_dir / active["publication_id"]
    if damage == "pointer_json":
        pointer.write_text("{broken")
    elif damage == "pointer_digest":
        pointer.write_text(json.dumps({**active, "manifest_sha256": "0" * 64}))
    elif damage == "manifest_schema":
        manifest = package / "manifest.json"
        content = json.loads(manifest.read_text())
        content["schema_version"] = "unreadable-v999"
        manifest.write_text(json.dumps(content))
    elif damage == "database":
        with (package / "publication.duckdb").open("ab") as stream:
            stream.write(b"corruption")
    elif damage == "missing":
        package.rename(tmp_path / "removed")
    elif damage == "symlink":
        target = tmp_path / "elsewhere"
        package.rename(target)
        package.symlink_to(target, target_is_directory=True)
    before = file_bytes(settings.state_dir)
    code = (
        "selection_unavailable"
        if damage == "pointer_json"
        else "publication_unavailable"
    )
    for path in ("/health/ready", "/api/v1/publications/current"):
        response = client.get(path)
        error_code(response, 503, code)
        assert str(tmp_path) not in response.text
    assert client.get("/health/live").status_code == 200
    assert file_bytes(settings.state_dir) == before


@pytest.mark.parametrize("exception", [PermissionError, duckdb.IOException, ValueError])
def test_storage_failures_do_not_leak_paths_or_masquerade_as_bad_input(
    client, active, monkeypatch, exception
):
    def unavailable(*args, **kwargs):
        raise exception("/private/operator/credentials must not appear")

    monkeypatch.setattr(data_api.queries, "query", unavailable)
    response = client.get(f"/api/v1/publications/{active['publication_id']}/overview")
    error_code(response, 503, "publication_unavailable")
    assert "/private" not in response.text


def test_unexpected_failure_is_json_without_trace(settings, active, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("private internal detail")

    monkeypatch.setattr(data_api.queries, "query", broken)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/v1/publications/{active['publication_id']}/overview"
        )
    error_code(response, 500, "internal_error")
    assert "private internal detail" not in response.text


def test_no_mutation_routes_and_unknown_api_is_json(client, settings, active):
    before = file_bytes(settings.publications_dir.parent)
    error_code(
        client.post(
            "/api/v1/publications/current", json={"publication_id": str(uuid4())}
        ),
        405,
        "method_not_allowed",
    )
    error_code(client.post("/api/v1/publications/activate"), 404, "route_not_found")
    error_code(client.get("/api/v1/missing"), 404, "route_not_found")
    assert file_bytes(settings.publications_dir.parent) == before
