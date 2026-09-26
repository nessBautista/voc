import hashlib

import pandas as pd
import pytest
from conftest import row

from src.preparation.dataset import COLUMNS, prepare_dataset


def test_exact_schema_original_text_and_no_mutation():
    r = row(1)
    r["text"] = "  ¡Buen servicio!  "
    r["title"] = " Title "
    blank = row(2)
    blank["text"] = "  "
    f = pd.DataFrame([r, blank])
    before = f.copy(deep=True)
    out = prepare_dataset(f)
    pd.testing.assert_frame_equal(f, before)
    assert list(out.data) == COLUMNS
    assert out.data.text.iloc[0] == "  ¡Buen servicio!  "
    assert out.data.model_text.iloc[0] == "¡Buen servicio!"
    assert (
        out.data.text_hash.iloc[0]
        == hashlib.sha256("¡Buen servicio!".encode()).hexdigest()
    )
    assert str(out.data.rating.dtype) == "Int64"
    assert out.report["excluded"][0]["reason"] == "blank_text"
    assert (
        prepare_dataset(f, {"combine_title_body": True}).data.model_text.iloc[0]
        == "Title\n¡Buen servicio!"
    )
    assert len(prepare_dataset(f, {"language_filter": ["es"]}).data) == 0


def test_invalid_schema_and_rules_fail():
    f = pd.DataFrame([row(1)])
    with pytest.raises(ValueError):
        prepare_dataset(f, {"unknown": True})
    f.loc[0, "rating"] = 6
    with pytest.raises(ValueError):
        prepare_dataset(f)


def test_nullable_day_precision_and_timestamp_recovery():
    day = row(1)
    day.update(
        app_id=None,
        rating=None,
        language=None,
        record_date="2026-09-10",
        date_precision="day",
        date_timezone="unknown",
        date_basis="provider_published_at",
        preferred_provider="appbot",
    )
    second = row(2)
    second["record_date"] = "2026-09-10"
    out = prepare_dataset(pd.DataFrame([day, second]))
    assert out.data.record_date.tolist() == ["2026-09-10", "2026-09-10T12:00:00+00:00"]
    assert out.report["date_normalizations"] == 1
    assert pd.isna(out.data.rating.iloc[0]) and pd.isna(out.data.app_id.iloc[0])
    assert out.data.date_timezone.iloc[0] == "unknown"


def test_text_and_rating_edits_have_different_hash_effects():
    base = pd.DataFrame([row(1)])
    first = prepare_dataset(base).data
    rated = base.copy()
    rated.loc[0, "rating"] = 5
    assert prepare_dataset(rated).data.text_hash.iloc[0] == first.text_hash.iloc[0]
    edited = base.copy()
    edited.loc[0, "text"] = "A new opinion"
    assert prepare_dataset(edited).data.text_hash.iloc[0] != first.text_hash.iloc[0]
    assert prepare_dataset(edited).data.record_id.iloc[0] == first.record_id.iloc[0]
