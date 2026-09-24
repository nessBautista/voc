import json
from datetime import datetime

import pandas as pd
import pytest

from voc._sources import Response
from voc.store import RawStore


def row(i, provider="play", app="example.app"):
    native = str(i)
    result = {
        "record_id": f"{provider}:{app}:{native}",
        "platform": "google_play" if provider == "play" else "app_store",
        "app_id": app,
        "country": "MX",
        "text": "Original " + native,
        "title": None,
        "rating": 3,
        "record_date": "2026-09-10T12:00:00+00:00",
        "date_basis": "source_updated_at",
        "date_precision": "second",
        "date_timezone": "UTC",
        "language": None,
        "language_basis": "not detected",
        "relevance": "app_target_match",
        "preferred_provider": provider,
        "identity_basis": "provider_identity",
        "source_count": 1,
        "source_record_ids_json": json.dumps([native]),
        "updated_at": "2026-09-10T12:00:00+00:00",
        "collected_at": "2026-09-10T12:00:00+00:00",
    }
    for prov in ("play", "apple_rss"):
        for key in (
            "source_id",
            "target",
            "text",
            "title",
            "rating",
            "app_version",
            "updated_at",
            "raw",
            "fetched_at",
            "capture_ref",
            "provider_record_id",
        ):
            result[prov + "__" + key] = None
    for key, value in {
        "source_id": native,
        "target": app,
        "text": result["text"],
        "title": None,
        "rating": 3,
        "app_version": "1.0",
        "updated_at": result["record_date"],
    }.items():
        result[provider + "__" + key] = value
    return result


@pytest.fixture
def store(tmp_path):
    s = RawStore(tmp_path / "raw.db")
    s.initialize(pd.DataFrame([row(i) for i in range(100)]), {"fixture": True})
    return s


def observation(i, **changes):
    return {
        "provider": "play",
        "app_id": "example.app",
        "source_id": str(i),
        "country": "mx",
        "text": "Original " + str(i),
        "title": None,
        "rating": 3,
        "app_version": "1.0",
        "updated_at": "2026-09-10T12:00:00+00:00",
        "fetched_at": "2026-09-11T12:00:00+00:00",
    } | changes


def report():
    return {
        "status": "success",
        "sources": {p: {"status": "success"} for p in ("apple_rss", "play")},
    }


def apple(id="new-apple", date="2026-09-10T12:00:00Z"):
    return {
        "id": {"label": id},
        "updated": {"label": date},
        "content": {"label": "Synthetic review"},
        "title": {"label": "Title"},
        "im:rating": {"label": "2"},
    }


def apple_page(*entries):
    return Response(200, json.dumps({"feed": {"entry": list(entries)}}).encode())


def play(id="new-play", date="2026-09-10T12:00:00+00:00"):
    return [
        id,
        ["Synthetic author"],
        2,
        None,
        "Synthetic Play review",
        [int(datetime.fromisoformat(date).timestamp())],
        0,
        None,
        None,
        None,
        "1.0",
    ]


def play_page(entries, token=None):
    payload = [entries, [None, token], None]
    return Response(
        200,
        (")]}'\n\n" + json.dumps([["wrb.fr", "oCPfdb", json.dumps(payload)]])).encode(),
    )


class Replay:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def request(self, url, body, **kwargs):
        self.calls.append(url)
        result = next(self.pages)
        if isinstance(result, Exception):
            raise result
        return result


def config(tmp_path, store):
    return {
        "raw_store": str(store.path),
        "collector_dir": str(tmp_path / "collector"),
        "update": {"since": "2026-09-09T00:00:00Z", "until": "2026-09-12T00:00:00Z"},
        "sources": [
            {
                "provider": "apple_rss",
                "app_id": "123",
                "country": "mx",
                "max_requests": 10,
                "max_items": 500,
            },
            {
                "provider": "play",
                "app_id": "example.app",
                "country": "mx",
                "lang": "es",
                "max_requests": 4,
                "max_items": 400,
            },
        ],
    }
