"""Compiled assets coexist with JSON APIs and never expose storage paths."""

import pytest
from fastapi.testclient import TestClient
from voc_app.main import create_app
from voc_app.settings import Settings


@pytest.fixture
def web_settings(settings, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('<!doctype html><div id="root">VOC</div>')
    (web / "assets").mkdir()
    (web / "assets/main.js").write_text('console.log("VOC")')
    return Settings(settings.publications_dir, settings.state_dir, web)


def test_serves_web_and_assets_without_an_active_publication(web_settings):
    with TestClient(create_app(web_settings)) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "text/html" in root.headers["content-type"]
        assert 'id="root"' in root.text
        assert root.headers["cache-control"] == "no-store"
        assert client.get("/assets/main.js").status_code == 200
        assert client.get("/?conversation=some-id").text == root.text
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert (
            client.get("/api/v1/publications/current").json()["error"]["code"]
            == "no_active_publication"
        )


def test_api_takes_precedence_over_static_files(web_settings, active):
    with TestClient(create_app(web_settings)) as client:
        assert (
            client.get("/api/v1/publications/current").json()["publication_id"]
            == active["publication_id"]
        )
        assert (
            client.get(
                f"/api/v1/publications/{active['publication_id']}/overview"
            ).json()["data"]["row_count"]
            == 12
        )
        assert client.get("/openapi.json").status_code == 200


@pytest.mark.parametrize(
    "path",
    ["api/missing", "health/missing", "mcp", "docs/missing", "assets/missing.js"],
)
def test_unknown_routes_are_errors_not_html(web_settings, path):
    # A compiled build cannot shadow a reserved API namespace.
    target = web_settings.web_dir / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not path.startswith("assets/"):
        target.write_text("must not be served")
    with TestClient(create_app(web_settings)) as client:
        response = client.get("/" + path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == (
            "mcp_disabled" if path == "mcp" else "route_not_found"
        )


def test_static_files_cannot_escape_the_build_directory(web_settings, tmp_path):
    private = tmp_path / "private.txt"
    private.write_text("PRIVATE-CONTENT")
    (web_settings.web_dir / "link.txt").symlink_to(private)
    with TestClient(create_app(web_settings)) as client:
        for path in (
            "/link.txt",
            "/%2e%2e/private.txt",
            "/catalog/publication.duckdb",
            "/state/active.json",
        ):
            response = client.get(path)
            assert response.status_code == 404
            assert "PRIVATE-CONTENT" not in response.text


def test_explicit_missing_web_build_fails_at_startup(settings, tmp_path):
    with pytest.raises(ValueError, match="compiled index.html"):
        create_app(
            Settings(
                settings.publications_dir, settings.state_dir, tmp_path / "missing"
            )
        )


def test_web_directory_is_explicit_environment_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("VOC_WEB_DIR", str(tmp_path / "dist"))
    assert Settings.from_env().web_dir == tmp_path / "dist"
    monkeypatch.setenv("VOC_WEB_DIR", "relative/path")
    with pytest.raises(ValueError, match="absolute local"):
        Settings.from_env()


def test_the_analysis_page_is_a_second_entry_under_dashboard(web_settings):
    # The web build ships dashboard/index.html; the static mount serves it at
    # /dashboard/ and redirects the bare path, with no HTML fallback elsewhere.
    page = web_settings.web_dir / "dashboard"
    page.mkdir()
    (page / "index.html").write_text('<!doctype html><div id="root">panel</div>')
    with TestClient(create_app(web_settings)) as client:
        served = client.get("/dashboard/")
        assert served.status_code == 200 and "panel" in served.text
        bare = client.get("/dashboard", follow_redirects=False)
        assert bare.status_code in (301, 307) and bare.headers["location"].endswith(
            "/dashboard/"
        )
        assert client.get("/panel/").status_code == 404
