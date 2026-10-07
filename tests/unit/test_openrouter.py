"""OpenRouter wiring and temporary credentials; all HTTP calls stay in memory."""

import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from voc_ml.llm import create_openrouter_client
from voc_ml.llm.openrouter import _load_key

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY_FILE", str(tmp_path / "session-key"))


def test_missing_key_fails_before_client_creation():
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY is missing"):
        create_openrouter_client()


@pytest.mark.parametrize("key", ["op://test/item/credential", "synthetic key"])
def test_unresolved_or_malformed_key_is_rejected_without_echoing_it(key):
    with pytest.raises(ValueError) as error:
        create_openrouter_client(api_key=key)
    assert key not in str(error.value)


def test_credential_precedence(monkeypatch):
    path = Path(os.environ["OPENROUTER_API_KEY_FILE"])
    path.write_text("synthetic-file-key\n")
    assert _load_key() == "synthetic-file-key"
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-environment-key")
    assert _load_key() == "synthetic-environment-key"
    assert _load_key("synthetic-explicit-key") == "synthetic-explicit-key"


def test_factory_uses_openrouter_and_sdk_retry_policy(monkeypatch):
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return "client"

    monkeypatch.setattr("openai.OpenAI", factory)
    assert create_openrouter_client(api_key="synthetic-key") == "client"
    assert captured == {
        "api_key": "synthetic-key",
        "base_url": "https://openrouter.ai/api/v1",
        "timeout": 60.0,
        "max_retries": 2,
    }


def test_chat_request_uses_openrouter_with_explicit_model():
    def respond(request):
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-key"
        payload = json.loads(request.content)
        assert payload["model"] == "test/model"
        assert payload["max_tokens"] == 12
        return httpx.Response(200, json={
            "id": "offline-test", "object": "chat.completion", "created": 0,
            "model": "test/model", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "Acceso a la cuenta"}}],
        })

    with create_openrouter_client(api_key="synthetic-key", max_retries=0) as client:
        # Replace the transport before any request; no real network is used.
        with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
            response = client.with_options(http_client=http_client).chat.completions.create(
                model="test/model", messages=[{"role": "user", "content": "Label this topic"}],
                max_tokens=12,
            )
        assert response.choices[0].message.content == "Acceso a la cuenta"


def test_session_injection_is_private_and_readable_by_existing_process():
    key = "synthetic-session-key"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/load-openrouter-key.py")],
        input=key, text=True, capture_output=True, check=True,
    )
    path = Path(os.environ["OPENROUTER_API_KEY_FILE"])
    assert path.read_text() == key
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    assert key not in result.stdout + result.stderr
    assert _load_key() == key
    # A failed op read must not destroy the working key.
    failed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/load-openrouter-key.py")],
        input="", text=True, capture_output=True,
    )
    assert failed.returncode != 0
    assert path.read_text() == key
