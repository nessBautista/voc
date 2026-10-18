"""Application configuration stays independent of the Operator environment."""

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from voc_app.settings import Settings

REPO = Path(__file__).resolve().parents[4]


def test_settings_use_only_explicit_application_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VOC_PUBLICATIONS_DIR", str(tmp_path / "catalog"))
    monkeypatch.setenv("VOC_APP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOC_STORAGE_CONFIG", "/must/not/be/read.toml")
    monkeypatch.setenv("VOC_DATA_DIR", "/operator/data")
    assert Settings.from_env() == Settings(tmp_path / "catalog", tmp_path / "state")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("value", ["", "relative/path", "s3://bucket/catalog"])
def test_settings_reject_ambiguous_paths(monkeypatch, value):
    monkeypatch.setenv("VOC_PUBLICATIONS_DIR", value)
    with pytest.raises(ValueError, match="absolute local"):
        Settings.from_env()


def test_server_starts_without_operator_modules_or_credentials(tmp_path):
    # A fresh interpreter catches accidental eager imports hidden by test fixtures.
    script = """
import sys
from voc_app.main import app
assert app.title == 'VOC data-state API'
for name in ('torch', 'zenml', 'mlflow', 'voc_ml', 'voc_dev', 'voc.datasets', 'boto3'):
    assert name not in sys.modules, name
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AWS_", "VOC_", "OPENROUTER_"))
    }
    subprocess.run([sys.executable, "-c", script], env=env, cwd=tmp_path, check=True)
    assert not list(tmp_path.iterdir())


def test_container_mounts_only_public_catalog_and_state():
    compose = yaml.safe_load((REPO / "docker/app/compose.yaml").read_text())
    assert compose["name"] == "voc-state-app"
    assert set(compose["services"]) == {"app"}
    app = compose["services"]["app"]
    assert app["ports"] == ["127.0.0.1:${VOC_APP_PORT:-8091}:8000"]
    assert "env_file" not in app
    assert set(app["environment"]) == {"VOC_PUBLICATIONS_DIR", "VOC_APP_STATE_DIR"}
    assert {mount["source"] for mount in app["volumes"]} == {
        "./data/voc-app/catalog",
        "./data/voc-app/state",
    }
    assert app["volumes"][0]["read_only"] is True
    assert all(mount["bind"]["create_host_path"] is False for mount in app["volumes"])
    assert "depends_on" not in app
    assert app["read_only"] is True


def test_application_lock_has_no_ml_or_notebook_runtime():
    lock = tomllib.loads((REPO / "apps/voc-app/server/uv.lock").read_text())
    packages = {package["name"] for package in lock["package"]}
    assert {"voc", "voc-app", "duckdb", "fastapi", "uvicorn", "httpx"} <= packages
    assert packages.isdisjoint(
        {
            "torch",
            "transformers",
            "zenml",
            "mlflow",
            "marimo",
            "jupyterlab",
            "bertopic",
            "voc-operator",
        }
    )
