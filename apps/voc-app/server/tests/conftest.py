"""Build the tutorial's synthetic publications; never use the developer's catalog."""

from pathlib import Path
from runpy import run_path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fake_cognito import DOMAIN, ISSUER, NATIVE, WEB, FakeCognito
from fastapi.testclient import TestClient
from voc.publications.builder import build_bundle
from voc.publications.store import activate_bundle

from voc_app.main import create_app
from voc_app.settings import Settings

REPO = Path(__file__).resolve().parents[4]
helpers = run_path(str(REPO / "tests/unit/test_publication_builder.py"))


@pytest.fixture(scope="session")
def bundles(tmp_path_factory):
    root = tmp_path_factory.mktemp("app-publications")
    return tuple(
        build_bundle(
            helpers["fixture_description"](name),
            root / name,
        )
        for name in ("example_a", "example_b")
    )


@pytest.fixture
def settings(tmp_path):
    return Settings(
        publications_dir=tmp_path / "catalog",
        state_dir=tmp_path / "state",
    )


@pytest.fixture
def active(bundles, settings):
    return activate_bundle(
        bundles[0],
        settings.publications_dir,
        settings.state_dir,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as instance:
        yield instance


@pytest.fixture(scope="session")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def cognito(key):
    return FakeCognito(key)


@pytest.fixture
def cognito_settings(tmp_path):
    return Settings(
        tmp_path / "catalog",
        tmp_path / "state",
        auth_mode="cognito",
        cognito_issuer=ISSUER,
        cognito_domain=DOMAIN,
        cognito_web_client_id=WEB,
        cognito_native_client_id=NATIVE,
        cognito_web_client_secret="test-secret-value",
    )


@pytest.fixture
def cognito_active(bundles, cognito_settings):
    return activate_bundle(
        bundles[0],
        cognito_settings.publications_dir,
        cognito_settings.state_dir,
    )


@pytest.fixture
def web(cognito_settings, cognito):
    """A cognito-mode app whose Cognito is the fake; sign in with ``sign_in``."""
    with TestClient(
        create_app(cognito_settings, oidc_transport=cognito.transport)
    ) as instance:
        yield instance
