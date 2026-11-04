"""OpenRouter boundaries: public catalog and one isolated, non-retrying request."""

import json
import os
import subprocess
import sys
import tomllib
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from voc.embeddings.shared.contracts import canonical_json, fingerprint

URL = "https://openrouter.ai/api/v1"
MODEL = "google/gemini-2.5-flash"
PROVIDER = "google-ai-studio"


def load_provider(path):
    with Path(path).open("rb") as stream:
        config = tomllib.load(stream)
    expected = {
        "model": MODEL,
        "provider": PROVIDER,
        "provider_name": "Google AI Studio",
        "prompt_usd_per_million": "0.30",
        "completion_usd_per_million": "2.50",
        "input_token_ceiling": 32768,
        "output_token_ceiling": 800,
        "max_cost_usd": "1.00",
        "max_attempts": 72,
        "request_seconds": 60,
        "text_policy": "prepared-verbatim-no-record-ids-v1",
    }
    if config != expected:
        raise ValueError(
            "Provider policy differs from the reviewed bounded configuration"
        )
    return config


def public_catalog():
    """Public metadata only. Does not read a credential or invoke a model."""
    with urllib.request.urlopen(
        URL + "/models/" + MODEL + "/endpoints", timeout=30
    ) as response:
        raw = response.read(2**20 + 1)
    if len(raw) > 2**20:
        raise ValueError("Catalog too large")
    return json.loads(raw)


def check_catalog(config, *, fetch=public_catalog):
    catalog = fetch()
    if catalog["data"]["id"] != config["model"]:
        raise ValueError("Catalog model differs")
    endpoints = [
        e for e in catalog["data"]["endpoints"] if e.get("tag") == config["provider"]
    ]
    if len(endpoints) != 1:
        raise ValueError("Pinned provider endpoint unavailable")
    endpoint = endpoints[0]
    required = {
        "response_format",
        "structured_outputs",
        "temperature",
        "max_tokens",
        "reasoning",
    }
    if (
        not required <= set(endpoint["supported_parameters"])
        or endpoint.get("status") != 0
    ):
        raise ValueError("Provider not ready for the required parameters")
    for key, ceiling in (
        ("prompt", "prompt_usd_per_million"),
        ("completion", "completion_usd_per_million"),
    ):
        price = Decimal(str(endpoint["pricing"][key]))
        if (
            not price.is_finite()
            or not 0 <= price <= Decimal(config[ceiling]) / 1000000
        ):
            raise ValueError("Provider price outside frozen ceiling")
    if Decimal(str(endpoint["pricing"].get("request", "0"))) != 0:
        raise ValueError("Per-request fees are outside this policy")
    if (
        endpoint["context_length"]
        < config["input_token_ceiling"] + config["output_token_ceiling"]
    ):
        raise ValueError("Provider context too small")
    if endpoint["max_completion_tokens"] < config["output_token_ceiling"]:
        raise ValueError("Provider output limit too small")
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "catalog_sha256": fingerprint(catalog),
        "endpoint": endpoint,
        "model_requests": 0,
    }


def credential_available():
    value = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return bool(
        value and not value.startswith("op://") and not any(c.isspace() for c in value)
    )


def request_once(body, *, sdk=False):
    """One child owns the socket. Parent enforces a 60-second wall deadline.

    Key travels only through the inherited environment. No exception text or
    stderr is returned. A timeout/HTTP error has uncertain consumption.
    """
    if not credential_available():
        raise ValueError(
            "OPENROUTER_API_KEY is not available through container injection"
        )
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "voc_ml.topics.interpretation.provider",
                *(["--sdk"] if sdk else []),
            ],
            input=(
                json.dumps(body, ensure_ascii=False).encode()
                if sdk
                else canonical_json(body)
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 2**20:
            return {"transport_error": "uncertain"}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return {"transport_error": "uncertain"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_prototype_once(body):
    """Bounded SDK transport, preserving request/schema order across the process."""
    return request_once(body, sdk=True)


def sdk_request(body, *, client_factory=None):
    """Same SDK call as voc-dev. Client injection permits offline wire tests."""
    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI
    with client_factory(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=URL,
        timeout=60.0,
        max_retries=0,
    ) as client:
        return client.chat.completions.create(**body).model_dump(mode="json")


def _send():
    try:
        raw_body = sys.stdin.buffer.read(2**20)
        body = json.loads(raw_body)
        if sys.argv[1:] == ["--sdk"]:
            sys.stdout.write(json.dumps(sdk_request(body), ensure_ascii=False))
            return
        request = urllib.request.Request(
            URL + "/chat/completions",
            data=raw_body,
            method="POST",
            headers={
                "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"].strip(),
                "Content-Type": "application/json",
            },
        )
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=55) as response:
            raw = response.read(2**20 + 1)
        if len(raw) > 2**20:
            raise ValueError("Response too large")
        json.loads(raw)
        sys.stdout.buffer.write(raw)
    except Exception:  # noqa: BLE001 - never expose headers, keys or error text
        sys.stdout.write('{"transport_error":"uncertain"}')


if __name__ == "__main__":
    _send()
