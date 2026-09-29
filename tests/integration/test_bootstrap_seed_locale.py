"""Fresh-store regressions using the unified seed's provider locale representation."""

import hashlib
import json
import sqlite3

import pandas as pd
import pytest
from tests.integration.conftest import Replay, apple, apple_page, config, play, play_page, row

from voc.collection.service import _boundary, initialize_raw_store, update
from voc.collection.config import validate_config
from voc.collection.store import RawStore


@pytest.mark.parametrize(
    "provider,app", [("play", "example.app"), ("apple_rss", "123")]
)
@pytest.mark.parametrize("representation", ["json", "country", "missing"])
def test_bootstrap_reads_native_storefront_and_excludes_other_countries(
    tmp_path, provider, app, representation
):
    native = row("native", provider, app)
    native[provider + "__locale"] = {
        "json": json.dumps(
            {"country": "mx", "lang": "es" if provider == "play" else None}
        ),
        "country": "mx",
        "missing": None,
    }[representation]
    foreign = row("foreign", provider, app)
    foreign[provider + "__locale"] = json.dumps({"country": "us", "lang": "es"})
    # Canonical country must not override a known native-source storefront.
    foreign[provider + "__updated_at"] = "2026-09-11T12:00:00+00:00"
    store = RawStore(tmp_path / "raw.db")
    store.initialize(pd.DataFrame([native, foreign]), {"fixture": True})
    cfg = config(tmp_path, store)
    del cfg["update"]["since"]
    cfg = validate_config(cfg)
    source = next(s for s in cfg["sources"] if s["provider"] == provider)
    assert _boundary(store, source, cfg["update"]) == (
        "2026-09-03T12:00:00+00:00",
        "overlap",
    )
    assert store.checkpoint(f"{provider}:{app}:mx") is None


def test_first_update_after_unified_seed_import_needs_no_manual_since(tmp_path):
    rows = [row("seed-play"), row("seed-apple", "apple_rss", "123")]
    for item in rows:
        provider = item["preferred_provider"]
        item[provider + "__locale"] = json.dumps(
            {"country": "mx", "lang": "es" if provider == "play" else None}
        )
    seed = tmp_path / "dataset.db"
    with sqlite3.connect(seed) as c:
        pd.DataFrame(rows).to_sql("dataset", c, index=False)
    seed_hash = hashlib.sha256(seed.read_bytes()).hexdigest()
    store = RawStore(tmp_path / "raw.db")
    parent = initialize_raw_store(store.path, seed_path=seed)
    cfg = config(tmp_path, store)
    del cfg["update"]["since"]
    pages = (
        [apple_page(apple(), apple("old", "2026-09-02T00:00:00Z"))]
        + [apple_page()] * 9
        + [play_page([play()])]
    )
    result = update(config=cfg, transport=Replay(pages)).require_success()
    assert result.revision_id != parent
    assert len(store.read().data) == 5
    assert len(store.read(parent).data) == 2
    for source in cfg["sources"]:
        assert (
            result.report["sources"][source["provider"]]["since"]
            == "2026-09-03T12:00:00+00:00"
        )
        assert (
            store.checkpoint(":".join([source["provider"], source["app_id"], "mx"]))
            is not None
        )
    assert hashlib.sha256(seed.read_bytes()).hexdigest() == seed_hash
