"""Pure request preparation. No SDK, credentials, provider or network access."""

import hashlib
import math
from dataclasses import dataclass

from voc.embeddings.shared.contracts import canonical_json, fingerprint
from voc.topics.interpretation import RESPONSE_VERSION, response_schema, validate_case


@dataclass(frozen=True)
class Prompt:
    variant: str
    version: str
    text: str

    def __post_init__(self):
        if (
            self.variant not in ("P0", "P1", "P2")
            or not isinstance(self.version, str)
            or not self.version.strip()
            or not isinstance(self.text, str)
            or not self.text.strip()
        ):
            raise ValueError("Invalid prompt")

    @property
    def sha256(self):
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Generation:
    model: str
    temperature: float = 0.0
    max_tokens: int = 800

    def __post_init__(self):
        if (
            not isinstance(self.model, str)
            or not self.model
            or any(c.isspace() for c in self.model)
        ):
            raise ValueError("Explicit model identifier required")
        if type(self.max_tokens) is not int or not 100 <= self.max_tokens <= 2000:
            raise ValueError("max_tokens outside supported bounds")
        if (
            type(self.temperature) not in (int, float)
            or not math.isfinite(self.temperature)
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError("Invalid temperature")


def build_request(case, representation, prompt, generation):
    validate_case(case)
    if representation not in ("R0", "R2"):
        raise ValueError("Unknown representation")
    payload = {
        "topic_id": case["topic_id"],
        "keywords": case["keywords"][representation],
        # Actual record identifiers and full-source metadata stay local.
        "evidence": [
            {"label": e["label"], "text": e["text"], "truncated": e["truncated"]}
            for e in case["evidence"]
        ],
    }
    body = {
        "model": generation.model,
        "temperature": generation.temperature,
        "max_tokens": generation.max_tokens,
        "messages": [
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": canonical_json(payload).decode()},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "voc_topic",
                "strict": True,
                "schema": response_schema([e["label"] for e in case["evidence"]]),
            },
        },
    }
    identity = {
        "body": body,
        "prompt_version": prompt.version,
        "prompt_sha256": prompt.sha256,
        "response_version": RESPONSE_VERSION,
        "evidence_sha256": case["evidence_sha256"],
    }
    return {
        "topic_id": case["topic_id"],
        "representation": representation,
        "prompt": prompt.variant,
        "request": identity,
        "request_sha256": fingerprint(identity),
        "evidence_ids": [e["record_id"] for e in case["evidence"]],
        "provider_ready": False,
    }
