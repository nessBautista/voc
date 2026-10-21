"""Browser sign-in and token checks run against a fake Cognito; no AWS is involved."""

import base64
import hashlib
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fake_cognito import DOMAIN, ORIGIN, SCOPE, WEB, access_token, mint


@pytest.fixture(scope="module")
def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def start_login(web):
    started = web.get("/auth/login", follow_redirects=False)
    assert started.status_code == 302
    location = urlsplit(started.headers["location"])
    assert (
        f"{location.scheme}://{location.netloc}{location.path}"
        == f"{DOMAIN}/oauth2/authorize"
    )
    return {name: values[0] for name, values in parse_qs(location.query).items()}


def sign_in(web, cognito):
    query = start_login(web)
    cognito.nonce = query["nonce"]
    return query, web.get(
        "/auth/callback",
        params={"code": "test-code", "state": query["state"]},
        follow_redirects=False,
    )


def test_login_redirects_to_cognito_with_pkce_and_our_callback(web):
    query = start_login(web)
    assert query["response_type"] == "code"
    assert query["client_id"] == WEB
    assert query["redirect_uri"] == f"{ORIGIN}/auth/callback"
    assert query["scope"] == SCOPE
    assert query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 32 and len(query["nonce"]) >= 32


def test_callback_exchanges_the_code_and_sets_an_httponly_cookie(
    web, cognito, cognito_settings
):
    query, finished = sign_in(web, cognito)
    assert finished.status_code == 303 and finished.headers["location"] == "/"
    cookie = finished.headers["set-cookie"]
    assert (
        cookie.startswith("voc_session=")
        and "HttpOnly" in cookie
        and "SameSite=lax" in cookie
        and "Path=/" in cookie
    )
    # The exchange carried the code, the callback, the PKCE verifier and the client secret.
    exchange = cognito.requests[0]
    form = {
        name: values[0] for name, values in parse_qs(exchange.content.decode()).items()
    }
    assert form["grant_type"] == "authorization_code" and form["code"] == "test-code"
    assert form["redirect_uri"] == f"{ORIGIN}/auth/callback"
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(form["code_verifier"].encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert challenge == query["code_challenge"]
    assert (
        exchange.headers["authorization"]
        == "Basic " + base64.b64encode(f"{WEB}:test-secret-value".encode()).decode()
    )
    assert web.get("/auth/me").json() == {"email": "a@example.com", "mode": "cognito"}
    # The database keeps a hash of the cookie, never the cookie itself.
    value = finished.cookies["voc_session"]
    rows = (
        sqlite3.connect(cognito_settings.state_dir / "sessions.sqlite3")
        .execute("SELECT * FROM sessions")
        .fetchall()
    )
    assert len(rows) == 1 and all(value not in str(column) for column in rows[0])


def test_login_state_is_consumed_once(web, cognito):
    query, finished = sign_in(web, cognito)
    assert finished.status_code == 303
    web.cookies.clear()
    replay = web.get(
        "/auth/callback",
        params={"code": "test-code", "state": query["state"]},
        follow_redirects=False,
    )
    assert (
        replay.status_code == 303
        and replay.headers["location"] == "/?login_error=login_state_invalid"
    )
    assert "set-cookie" not in replay.headers
    assert len(cognito.requests) == 1
    assert web.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "params,error",
    [
        ({"code": "test-code", "state": "unknown-state"}, "login_state_invalid"),
        ({"state": None, "error": "access_denied"}, "login_cancelled"),
        ({"state": None}, "login_cancelled"),
    ],
)
def test_unknown_state_or_cancelled_login_creates_no_session(
    web, cognito, params, error
):
    query = start_login(web)
    params = {
        name: query["state"] if value is None else value
        for name, value in params.items()
    }
    finished = web.get("/auth/callback", params=params, follow_redirects=False)
    assert (
        finished.status_code == 303
        and finished.headers["location"] == f"/?login_error={error}"
    )
    assert "set-cookie" not in finished.headers
    assert cognito.requests == []
    assert web.get("/auth/me").status_code == 401


def test_callback_rejects_unexpected_parameters(web):
    assert (
        web.get(
            "/auth/callback", params={"code": "x", "state": "y", "return": "/"}
        ).status_code
        == 422
    )


def test_nonce_mismatch_and_refused_exchange_create_no_session(web, cognito):
    query = start_login(web)
    cognito.nonce = "another-browser"
    finished = web.get(
        "/auth/callback",
        params={"code": "test-code", "state": query["state"]},
        follow_redirects=False,
    )
    assert (
        finished.headers["location"] == "/?login_error=invalid_token"
        and "set-cookie" not in finished.headers
    )
    cognito.refuse = True
    query = start_login(web)
    finished = web.get(
        "/auth/callback",
        params={"code": "test-code", "state": query["state"]},
        follow_redirects=False,
    )
    assert (
        finished.headers["location"] == "/?login_error=token_exchange_failed"
        and "set-cookie" not in finished.headers
    )
    assert web.get("/auth/me").status_code == 401


def test_logout_requires_the_origin_header_and_ends_the_session(web, cognito):
    _, finished = sign_in(web, cognito)
    value = finished.cookies["voc_session"]
    assert web.post("/auth/logout").status_code == 403
    assert (
        web.post("/auth/logout", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    ended = web.post("/auth/logout", headers={"Origin": ORIGIN})
    assert ended.status_code == 200
    assert ended.json() == {
        "logout_url": f"{DOMAIN}/logout?client_id={WEB}&logout_uri=http%3A%2F%2Flocalhost%3A8091%2F"
    }
    assert 'voc_session=""' in ended.headers["set-cookie"]
    assert web.get("/auth/me").status_code == 401
    # A copied cookie value is dead too, because the server forgot the session.
    assert (
        web.get("/auth/me", headers={"Cookie": f"voc_session={value}"}).status_code
        == 401
    )


def test_expired_access_token_is_renewed_then_the_session_ends_when_renewal_fails(
    web, cognito
):
    cognito.expires_in = 0
    sign_in(web, cognito)
    assert web.get("/auth/me").status_code == 200
    renewal = {
        name: values[0]
        for name, values in parse_qs(cognito.requests[-1].content.decode()).items()
    }
    assert renewal["grant_type"] == "refresh_token"
    assert renewal["refresh_token"] == "refresh-after-authorization_code"
    cognito.refuse = True
    expired = web.get("/auth/me")
    assert (
        expired.status_code == 401
        and expired.json()["error"]["code"] == "session_expired"
    )


def test_bearer_access_token_from_the_native_client_is_accepted(web, key):
    response = web.get(
        "/auth/me", headers={"Authorization": "Bearer " + access_token(key)}
    )
    assert response.status_code == 200 and response.json()["mode"] == "cognito"


@pytest.mark.parametrize(
    "token_claims",
    [
        {"client_id": "someone-else"},
        {"scope": "openid email"},
        {"token_use": "id"},
        {"iss": "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_OTHER"},
        {"exp": int(time.time()) - 600},
    ],
)
def test_bearer_tokens_with_wrong_claims_are_rejected(web, key, token_claims):
    response = web.get(
        "/auth/me",
        headers={"Authorization": "Bearer " + access_token(key, **token_claims)},
    )
    assert (
        response.status_code == 401
        and response.json()["error"]["code"] == "invalid_token"
    )


def test_id_tokens_and_foreign_signatures_are_rejected_as_bearer(web, key, other_key):
    id_token = mint(key, aud=WEB, token_use="id", email="a@example.com", nonce="n")
    assert (
        web.get("/auth/me", headers={"Authorization": "Bearer " + id_token}).status_code
        == 401
    )
    forged = access_token(other_key)
    assert (
        web.get("/auth/me", headers={"Authorization": "Bearer " + forged}).status_code
        == 401
    )
    assert (
        web.get("/auth/me", headers={"Authorization": "Bearer not-a-token"}).status_code
        == 401
    )
    assert (
        web.get("/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401
    )


def test_cognito_mode_hides_interactive_documentation(web):
    assert web.get("/docs").status_code == 404
    assert web.get("/openapi.json").status_code == 404


def test_local_mode_has_a_fixed_identity_and_no_sign_in_routes(client):
    assert client.get("/auth/me").json() == {
        "email": "developer@localhost",
        "mode": "local",
    }
    assert client.get("/auth/login", follow_redirects=False).status_code == 404
    assert client.post("/auth/logout", headers={"Origin": ORIGIN}).status_code == 404
    assert client.get("/docs").status_code == 200
