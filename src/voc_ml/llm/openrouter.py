"""OpenRouter connection, adapted from Sonar's common/model_gateway client.

Creating a client does not send a request. Callers choose the model and explicitly
request generation. The returned SDK client can also be passed to BERTopic.
"""

import os
import tempfile
from pathlib import Path

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
SESSION_KEY_PATH = Path(tempfile.gettempdir()) / "voc-openrouter-api-key"


def _load_key(api_key=None):
    # Explicit key, then startup environment, then the temporary live-session bridge.
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY")
    if not key or not key.strip():
        path = Path(os.environ.get("OPENROUTER_API_KEY_FILE") or SESSION_KEY_PATH)
        try:
            key = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            key = ""
    key = key.strip()
    if not key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing. Configure the private credential file "
            "and run just up later, or use scripts/load-openrouter-key.py for this session."
        )
    if key.startswith("op://"):
        raise ValueError("Resolve the OpenRouter 1Password reference on the host first.")
    if any(char.isspace() for char in key):
        raise ValueError("The OpenRouter API key must be a single value without whitespace.")
    return key


def create_openrouter_client(*, api_key=None, timeout=60.0, max_retries=2):
    """Build a client without a network call; only the SDK handles retries.

    Recreate the client after changing its key. Environment credentials take
    precedence over the temporary file, which supports already-running notebooks.
    Use client.close() (or a with block) when a short-lived client is no longer needed.
    """
    key = _load_key(api_key)
    # Loading datasets or importing voc_ml.llm does not require the optional SDK.
    from openai import OpenAI

    return OpenAI(
        api_key=key,
        base_url=OPENROUTER_BASE_URL,
        timeout=timeout,
        max_retries=max_retries,
    )
