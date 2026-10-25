"""Exact Unicode-preserving collection JSON, digests, and timestamps."""

import hashlib
import json
from datetime import UTC, datetime


def now():
    return datetime.now(UTC).isoformat()


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()
