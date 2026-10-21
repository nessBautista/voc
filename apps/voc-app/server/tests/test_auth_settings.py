"""A cloud configuration without login fails at startup; local mode is unchanged."""

from pathlib import Path

import pytest

from voc_app.settings import Settings

ISSUER = "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_EXAMPLE"
DOMAIN = "https://example.auth.us-east-2.amazoncognito.com"
COGNITO = {
    "auth_mode": "cognito",
    "cognito_issuer": ISSUER,
    "cognito_domain": DOMAIN,
    "cognito_web_client_id": "web-client-id",
    "cognito_native_client_id": "native-client-id",
    "cognito_web_client_secret": "resolved-test-secret-value",
}


def settings(**overrides):
    return Settings(Path("/catalog"), Path("/state"), **overrides)


def test_local_mode_is_the_default_and_needs_no_cognito_values():
    assert settings().auth_mode == "local"
    assert settings().public_origin == "http://localhost:8091"


def test_cognito_mode_accepts_a_complete_configuration():
    configured = settings(**COGNITO)
    assert configured.cognito_scopes.split() == ["openid", "email", "voc-api/access"]
    # The secret must never appear in logs or error output.
    assert "resolved-test-secret-value" not in repr(configured)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cognito_issuer", "", "issuer URL"),
        ("cognito_issuer", "https://example.com/us-east-2_EXAMPLE", "issuer URL"),
        (
            "cognito_issuer",
            "http://cognito-idp.us-east-2.amazonaws.com/x",
            "issuer URL",
        ),
        ("cognito_domain", "", "login domain"),
        (
            "cognito_domain",
            "http://example.auth.us-east-2.amazoncognito.com",
            "login domain",
        ),
        ("cognito_web_client_id", "", "both app client IDs"),
        ("cognito_native_client_id", "", "both app client IDs"),
        ("cognito_web_client_secret", "", "resolved web client secret"),
        (
            "cognito_web_client_secret",
            "op://Vault/Item/field",
            "resolved web client secret",
        ),
        ("cognito_scopes", "openid email", "voc-api/access"),
    ],
)
def test_cognito_mode_refuses_to_start_with_a_missing_or_unresolved_value(
    field, value, message
):
    with pytest.raises(ValueError, match=message):
        settings(**{**COGNITO, field: value})


def test_cognito_mode_keeps_the_hosted_mcp_route_disabled():
    with pytest.raises(ValueError, match="VOC_MCP_ENABLED must be false"):
        settings(**COGNITO, mcp_enabled=True, mcp_token="x" * 40)


@pytest.mark.parametrize("value", ["", "none", "anonymous", "Cognito"])
def test_auth_mode_must_be_explicit(value):
    with pytest.raises(ValueError, match="local or cognito"):
        settings(auth_mode=value)


@pytest.mark.parametrize(
    "origin",
    [
        "http://10.0.0.5:8091",
        "http://127.0.0.1:8091",
        "https://user:pass@example.net",
        "https://example.net/app",
        "ftp://example.net",
        "",
    ],
)
def test_public_origin_is_localhost_or_https(origin):
    with pytest.raises(ValueError, match="VOC_PUBLIC_ORIGIN"):
        settings(public_origin=origin)


def test_public_origin_accepts_a_cloudfront_address():
    assert settings(public_origin="https://d123.cloudfront.net").public_origin


def test_from_env_reads_every_authentication_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("VOC_PUBLICATIONS_DIR", str(tmp_path / "catalog"))
    monkeypatch.setenv("VOC_APP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOC_AUTH_MODE", "cognito")
    monkeypatch.setenv("VOC_PUBLIC_ORIGIN", "http://localhost:8091")
    monkeypatch.setenv("VOC_COGNITO_ISSUER", ISSUER)
    monkeypatch.setenv("VOC_COGNITO_DOMAIN", DOMAIN)
    monkeypatch.setenv("VOC_COGNITO_WEB_CLIENT_ID", "web-client-id")
    monkeypatch.setenv("VOC_COGNITO_NATIVE_CLIENT_ID", "native-client-id")
    monkeypatch.setenv("VOC_COGNITO_WEB_CLIENT_SECRET", "resolved-test-secret-value")
    monkeypatch.setenv("VOC_COGNITO_SCOPES", "openid email voc-api/access")
    loaded = Settings.from_env()
    assert loaded == Settings(tmp_path / "catalog", tmp_path / "state", **COGNITO)


def test_from_env_in_cognito_mode_fails_without_the_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("VOC_PUBLICATIONS_DIR", str(tmp_path / "catalog"))
    monkeypatch.setenv("VOC_APP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOC_AUTH_MODE", "cognito")
    for name, value in COGNITO.items():
        if name.startswith("cognito_") and name != "cognito_web_client_secret":
            monkeypatch.setenv("VOC_" + name.upper(), value)
    monkeypatch.delenv("VOC_COGNITO_WEB_CLIENT_SECRET", raising=False)
    with pytest.raises(ValueError, match="resolved web client secret"):
        Settings.from_env()
