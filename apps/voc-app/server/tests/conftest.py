"""Build the tutorial's synthetic publications; never use the developer's catalog."""

from pathlib import Path
from runpy import run_path

import pytest
from fastapi.testclient import TestClient
from voc_app.main import create_app
from voc_app.settings import Settings

from voc.publications.builder import build_bundle
from voc.publications.store import activate_bundle

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
