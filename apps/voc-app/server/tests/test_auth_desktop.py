"""The Mac app learns the pool from the server and trades its token for a web session."""

import time
from uuid import uuid4

from fake_cognito import DOMAIN, ISSUER, NATIVE, ORIGIN, WEB, access_token, mint


def test_config_is_public_nonsecret_and_cognito_only(web, client):
    response = web.get("/auth/config")
    assert response.status_code == 200
    assert response.json() == {
        "issuer": ISSUER,
        "domain": DOMAIN,
        "native_client_id": NATIVE,
        "scopes": ["openid", "email", "voc-api/access"],
        "callback": "voc-data-state://auth/callback",
        "public_origin": ORIGIN,
    }
    assert "secret" not in response.text.lower()
    assert WEB not in response.text
    assert client.get("/auth/config").status_code == 404


def test_native_token_becomes_a_web_session_for_the_same_person(
    web, key, cognito_active
):
    token = access_token(key, sub="user-a", exp=int(time.time()) + 1800)
    exchanged = web.post(
        "/auth/desktop-session", headers={"Authorization": "Bearer " + token}
    )
    assert exchanged.status_code == 200, exchanged.text
    body = exchanged.json()
    assert body["email"] == "user-a@example.com"
    cookie = exchanged.headers["set-cookie"]
    assert cookie.startswith(f"voc_session={body['cookie']['value']}")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert 1700 <= body["cookie"]["max_age"] <= 1800
    assert "access-control-allow-origin" not in exchanged.headers
    # The web view and the native caller now read the same owned conversation.
    assert web.get("/auth/me").json() == {
        "email": "user-a@example.com",
        "mode": "cognito",
    }
    asked = web.post(
        "/api/v1/ask",
        json={
            "request_id": str(uuid4()),
            "question": "¿Cuántos registros tenemos de Google Play?",
        },
        headers={"Authorization": "Bearer " + token},
    )
    assert asked.status_code == 202, asked.text
    conversation = asked.json()["conversation_id"]
    web.headers.pop("Authorization", None)
    assert web.get(f"/api/v1/conversations/{conversation}").status_code == 200


def test_desktop_session_refuses_cookies_web_clients_and_foreign_tokens(
    web, key, cognito
):
    assert web.post("/auth/desktop-session").status_code == 401
    web_token = access_token(key, client_id=WEB)
    refused = web.post(
        "/auth/desktop-session", headers={"Authorization": "Bearer " + web_token}
    )
    assert (
        refused.status_code == 403
        and refused.json()["error"]["code"] == "native_token_required"
    )
    foreign = access_token(
        key, iss="https://cognito-idp.us-east-2.amazonaws.com/us-east-2_OTHER"
    )
    assert (
        web.post(
            "/auth/desktop-session", headers={"Authorization": "Bearer " + foreign}
        ).status_code
        == 401
    )
    id_token = mint(key, aud=WEB, token_use="id", email="a@example.com", nonce="n")
    assert (
        web.post(
            "/auth/desktop-session", headers={"Authorization": "Bearer " + id_token}
        ).status_code
        == 401
    )
    assert web.get("/auth/me").status_code == 401
    assert not any("/oauth2/token" in str(r.url) for r in cognito.requests)


def test_desktop_session_ends_with_the_token_and_cannot_renew(web, key, cognito):
    token = access_token(
        key, exp=int(time.time()) + 31
    )  # inside the 30 s leeway, already "expired" for sessions
    exchanged = web.post(
        "/auth/desktop-session", headers={"Authorization": "Bearer " + token}
    )
    assert exchanged.status_code == 200
    assert exchanged.json()["cookie"]["max_age"] <= 31
    short = access_token(key, exp=int(time.time()) - 5)
    assert (
        web.post(
            "/auth/desktop-session", headers={"Authorization": "Bearer " + short}
        ).status_code
        == 200
    )
    # A session issued for an expired token is dead on arrival and never calls the token endpoint.
    assert web.get("/auth/me").status_code == 401
    assert not any("/oauth2/token" in str(r.url) for r in cognito.requests)


def test_each_exchange_mints_a_fresh_session_value(web, key):
    token = access_token(key)
    first = web.post(
        "/auth/desktop-session", headers={"Authorization": "Bearer " + token}
    ).json()["cookie"]["value"]
    second = web.post(
        "/auth/desktop-session", headers={"Authorization": "Bearer " + token}
    ).json()["cookie"]["value"]
    assert first != second and len(first) >= 32
