import pytest
from conftest import Replay, apple, apple_page, config, play, play_page

from voc._sources import Response
from voc.collector import update


def test_two_source_commit_and_idempotent_retry(tmp_path, store):
    cfg = config(tmp_path, store)
    pages = (
        [apple_page(apple()), apple_page(apple("old", "2026-09-08T00:00:00Z"))]
        + [apple_page()] * 8
        + [play_page([play()])]
    )
    replay = Replay(pages)
    result = update(
        store=store.path, config=cfg, operation_id="both", transport=replay
    ).require_success()
    assert len(store.read().data) == 103
    assert result.report["counts"]["inserted"] == 3
    assert len(list((tmp_path / "collector/captures/both").rglob("*.response"))) == 11
    again = update(
        store=store.path, config=cfg, operation_id="both", transport=Replay([])
    )
    assert again.revision_id == result.revision_id


def test_apple_gap_blocks_both_commit_and_checkpoint(tmp_path, store):
    cfg = config(tmp_path, store)
    before = store.revision()["id"]
    result = update(
        config=cfg,
        transport=Replay(
            [apple_page(apple())] + [apple_page()] * 9 + [play_page([play()])]
        ),
    )
    assert result.report["sources"]["apple_rss"]["stop_reason"] == "feed_coverage_gap"
    with pytest.raises(RuntimeError):
        result.require_success()
    assert store.revision()["id"] == before
    assert store.checkpoint("play:example.app:mx") is None


def test_play_stops_only_on_wholly_old_page(tmp_path, store):
    cfg = config(tmp_path, store)
    replay = Replay(
        [apple_page(apple("old", "2026-09-08T00:00:00Z"))]
        + [apple_page()] * 9
        + [
            play_page([play("old", "2026-09-08T00:00:00+00:00"), play()], token="next"),
            play_page([play("older", "2026-09-07T00:00:00+00:00")], token="more"),
        ]
    )
    r = update(config=cfg, transport=replay).require_success()
    assert r.report["sources"]["play"]["requests"] == 2
    assert r.report["sources"]["play"]["stop_reason"] == "overlap_boundary"
    assert len(store.read().data) == 102


def test_http_failure_and_budget_do_not_commit(tmp_path, store):
    cfg = config(tmp_path, store)
    before = store.revision()["id"]
    r = update(
        config=cfg, transport=Replay([Response(429, b"limited"), play_page([play()])])
    )
    assert r.revision_id is None and store.revision()["id"] == before
    cfg["sources"][0]["max_requests"] = 1
    r = update(config=cfg, transport=Replay([apple_page(apple()), play_page([])]))
    assert r.report["sources"]["apple_rss"]["stop_reason"] == "request_limit"


def test_capture_size_budget(tmp_path, store):
    cfg = config(tmp_path, store)
    cfg["update"]["max_capture_bytes"] = 1
    r = update(config=cfg, transport=Replay([apple_page(apple()), play_page([])]))
    assert r.revision_id is None


def test_retry_failed_operation_keeps_attempts_and_no_duplicate_rows(tmp_path, store):
    cfg = config(tmp_path, store)
    failed = update(
        config=cfg,
        operation_id="retry",
        transport=Replay([Response(500, b"failure"), play_page([])]),
    )
    assert failed.revision_id is None
    pages = (
        [apple_page(apple("old", "2026-09-08T00:00:00Z"))]
        + [apple_page()] * 9
        + [play_page([play(), play()])]
    )
    result = update(
        config=cfg, operation_id="retry", transport=Replay(pages)
    ).require_success()
    assert len(list((tmp_path / "collector/captures/retry").iterdir())) == 2
    assert len(store.read().data) == 102
    assert result.report["counts"]["duplicates"] == 1


def test_invalid_identity_and_operation_path_are_rejected(tmp_path, store):
    cfg = config(tmp_path, store)
    with pytest.raises(ValueError):
        update(config=cfg, operation_id="../outside", transport=Replay([]))
    broken = apple()
    broken["id"] = {"label": None}
    result = update(config=cfg, transport=Replay([apple_page(broken), play_page([])]))
    assert result.revision_id is None


def test_boundary_bootstrap_uses_native_source_not_other_dates(tmp_path, store):
    from voc.collector import _boundary
    from voc.config import validate_config

    cfg = config(tmp_path, store)
    del cfg["update"]["since"]
    cfg = validate_config(cfg)
    boundary, basis = _boundary(store, cfg["sources"][1], cfg["update"])
    assert boundary == "2026-09-03T12:00:00+00:00" and basis == "overlap"
    with pytest.raises(ValueError):
        _boundary(store, cfg["sources"][0], cfg["update"])
