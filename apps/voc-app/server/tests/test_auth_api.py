"""Every data, chat and report route refuses strangers before touching storage or a model."""

import sqlite3
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fake_cognito import ORIGIN, access_token

PROTECTED = [
    ("get", "/api/v1/publications/current"),
    ("get", f"/api/v1/publications/{uuid4()}/overview"),
    ("post", "/api/v1/ask"),
    ("get", f"/api/v1/conversations/{uuid4()}"),
    ("post", f"/api/v1/conversations/{uuid4()}/reports"),
    ("get", f"/api/v1/reports/{uuid4()}"),
    ("get", f"/api/v1/reports/{uuid4()}/chunks"),
]


class NeverPlanner:
    """Fails loudly if a request reaches the planner without a recognised caller."""

    async def plan(self, question, history, mode="daily"):
        raise AssertionError("The planner ran for an unauthenticated request")


def sign_in(web, cognito):
    query = parse_qs(
        urlsplit(
            web.get("/auth/login", follow_redirects=False).headers["location"]
        ).query
    )
    cognito.nonce = query["nonce"][0]
    finished = web.get(
        "/auth/callback",
        params={"code": "test-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert finished.status_code == 303 and "voc_session" in finished.cookies


def payload():
    return {"request_id": str(uuid4()), "question": "¿Cuántos registros hay?"}


@pytest.mark.parametrize("method,path", PROTECTED)
def test_anonymous_requests_are_refused_before_any_work(
    cognito_settings, cognito, cognito_active, method, path
):
    from fastapi.testclient import TestClient

    from voc_app.main import create_app

    app = create_app(
        cognito_settings, planner=NeverPlanner(), oidc_transport=cognito.transport
    )
    with TestClient(app) as web:
        response = web.request(
            method.upper(), path, json=payload() if method == "post" else None
        )
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "sign_in_required"
    turns = sqlite3.connect(cognito_settings.state_dir / "conversations.sqlite3")
    assert turns.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0


def test_health_stays_open_but_names_no_publication(web, cognito_active):
    assert web.get("/health/live").json() == {"status": "alive"}
    assert web.get("/health/ready").json() == {"status": "ready"}


def test_a_signed_in_browser_reads_data_and_asks_only_from_our_page(
    web, cognito, cognito_active
):
    sign_in(web, cognito)
    current = web.get("/api/v1/publications/current")
    assert current.status_code == 200
    assert current.json()["publication_id"] == cognito_active["publication_id"]
    # Writes need the page's own origin; a cookie alone is not enough.
    assert web.post("/api/v1/ask", json=payload()).status_code == 403
    forged = web.post(
        "/api/v1/ask", json=payload(), headers={"Origin": "https://evil.example"}
    )
    assert forged.status_code == 403
    assert forged.json()["error"]["code"] == "origin_not_allowed"
    asked = web.post("/api/v1/ask", json=payload(), headers={"Origin": ORIGIN})
    assert asked.status_code in (200, 202), asked.text
    conversation = asked.json()["conversation_id"]
    assert web.get(f"/api/v1/conversations/{conversation}").status_code == 200


def test_the_native_client_uses_a_bearer_token_and_no_origin(web, key, cognito_active):
    headers = {"Authorization": "Bearer " + access_token(key)}
    assert web.get("/api/v1/publications/current", headers=headers).status_code == 200
    asked = web.post("/api/v1/ask", json=payload(), headers=headers)
    assert asked.status_code in (200, 202), asked.text
    stale = {"Authorization": "Bearer " + access_token(key, scope="openid email")}
    assert web.get("/api/v1/publications/current", headers=stale).status_code == 401
