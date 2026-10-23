"""Optional OpenRouter transport with explicit requests and no hidden retries."""

import os

BASE_URL = "https://openrouter.ai/api/v1"


def create_client():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key or key.startswith("op://") or any(c.isspace() for c in key):
        raise ValueError(
            "Configure OPENROUTER_API_KEY using the existing container credential injection"
        )
    from openai import OpenAI

    return OpenAI(api_key=key, base_url=BASE_URL, timeout=60.0, max_retries=0)


def request(client, body):
    """Return provider JSON only. Never persist headers, keys or exception messages."""
    try:
        response = client.chat.completions.create(**body)
        return response.model_dump(mode="json")
    except Exception:  # noqa: BLE001 - SDK exceptions may contain credentials or request bodies
        return {"error": "provider_error"}
