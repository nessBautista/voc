import json

import pandas as pd
import pytest
from conftest import observation, report, row

from voc.store import RawStore


def test_delta_history_retry_and_fresh_connection(store):
    first = store.revision()["id"]
    obs = (
        [observation(i) for i in range(100, 110)]
        + [observation(i, text="Edited") for i in (1, 2)]
        + [observation(3)]
    )
    second = store.commit(
        obs,
        parent=first,
        operation="update1",
        report=report(),
        checkpoints={"play:example.app:mx": "2026-09-12"},
    )
    assert (
        store.commit(
            obs, parent=first, operation="update1", report=report(), checkpoints={}
        )
        == second
    )
    reopened = RawStore(store.path)
    assert len(reopened.read().data) == 110
    assert len(reopened.read(first).data) == 100
    assert (
        reopened.read(first)
        .data.query('record_id == "play:example.app:1"')
        .text.iloc[0]
        == "Original 1"
    )
    assert (
        reopened.read().data.query('record_id == "play:example.app:1"').text.iloc[0]
        == "Edited"
    )
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM versions").fetchone()[0] == 112
        assert c.execute("SELECT count(*) FROM revisions").fetchone()[0] == 2
    assert json.loads(store.revision()["report"])["counts"] == {
        "inserted": 10,
        "edited": 2,
        "duplicates": 1,
        "stale": 0,
    }


def test_failed_partial_conflict_and_transaction_rollback(store):
    first = store.revision()["id"]
    bad = report()
    bad["sources"]["play"]["status"] = "failed"
    with pytest.raises(ValueError):
        store.commit([], parent=first, operation="bad", report=bad, checkpoints={})
    with pytest.raises(KeyError):
        store.commit(
            [observation(101), {}],
            parent=first,
            operation="broken",
            report=report(),
            checkpoints={"p": "today"},
        )
    assert store.revision()["id"] == first and store.checkpoint("p") is None
    store.commit([], parent=first, operation="noop", report=report(), checkpoints={})
    with pytest.raises(RuntimeError):
        store.commit(
            [], parent=first, operation="conflict", report=report(), checkpoints={}
        )


def test_seed_apple_identity_is_preserved(tmp_path):
    original = row("native", "apple_rss", "123")
    original["record_id"] = "appbot:historical-id"
    original["preferred_provider"] = "appbot"
    s = RawStore(tmp_path / "s.db")
    s.initialize(pd.DataFrame([original]), {})
    obs = observation("native", provider="apple_rss", app_id="123", text="Changed")
    s.commit(
        [obs],
        parent=s.revision()["id"],
        operation="edit",
        report=report(),
        checkpoints={},
    )
    assert list(s.read().data.record_id) == ["appbot:historical-id"]
    with pytest.raises(FileExistsError):
        s.initialize(pd.DataFrame([original]), {})


def test_second_update_retains_other_sources_and_fixed_revision(tmp_path):
    baseline = [row(1), row("a", "apple_rss", "123")]
    for provider, platform in [("x", "x"), ("youtube", "youtube")]:
        item = row(provider)
        item.update(
            record_id=provider + ":fixed",
            platform=platform,
            preferred_provider=provider,
        )
        item["play__source_id"] = None
        baseline.append(item)
    s = RawStore(tmp_path / "raw.db")
    seed = s.initialize(pd.DataFrame(baseline), {})
    a = s.commit(
        [observation(2)], parent=seed, operation="one", report=report(), checkpoints={}
    )
    before = s.read(a).data
    b = s.commit(
        [observation(3), observation(2, text="Edit")],
        parent=a,
        operation="two",
        report=report(),
        checkpoints={},
    )
    pd.testing.assert_frame_equal(before, s.read(a).data)
    assert len(s.read(b).data) == 6 and list(s.read(b).data) == list(before)
    for platform in ("x", "youtube"):
        pd.testing.assert_frame_equal(
            before.query("platform == @platform").reset_index(drop=True),
            s.read(b).data.query("platform == @platform").reset_index(drop=True),
        )


def test_stale_observation_cannot_replace_newer_content(store):
    parent = store.revision()["id"]
    store.commit(
        [observation(1, text="old copy", updated_at="2026-09-01T00:00:00+00:00")],
        parent=parent,
        operation="old",
        report=report(),
        checkpoints={},
    )
    assert (
        store.read().data.query('record_id == "play:example.app:1"').text.iloc[0]
        == "Original 1"
    )


def test_reusing_operation_with_different_payload_is_rejected(store):
    parent = store.revision()["id"]
    store.commit(
        [observation(101)],
        parent=parent,
        operation="same",
        report=report(),
        checkpoints={},
    )
    with pytest.raises(ValueError):
        store.commit(
            [observation(102)],
            parent=parent,
            operation="same",
            report=report(),
            checkpoints={},
        )
