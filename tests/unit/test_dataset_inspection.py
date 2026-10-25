"""Summary semantics are independent of source selection and presentation."""

import pandas as pd
import pytest

from voc.datasets import summary
from voc.models import Snapshot


def test_summary_uses_utc_months_preserves_metadata_and_input():
    frame = pd.DataFrame(
        {
            "platform": ["google_play", "app_store", "google_play", None, "app_store"],
            "record_date": [
                "2026-03-01T00:30:00+02:00",
                "2026-01-31T23:30:00-02:00",
                "2026-01-02",
                "not-a-date",
                None,
            ],
            "text": ["private review"] * 5,
        }
    )
    original = frame.copy(deep=True)
    metadata = {"release_id": "pinned-release"}
    result = summary(Snapshot(frame, metadata))
    assert result == {
        "info": metadata,
        "platform_counts": {"google_play": 2, "app_store": 2},
        "month_counts": {"2026-01": 1, "2026-02": 2},
        "unknown_dates": 2,
    }
    assert list(result["month_counts"]) == ["2026-01", "2026-02"]
    assert result["info"] is metadata
    pd.testing.assert_frame_equal(frame, original)


def test_summary_of_empty_snapshot():
    frame = pd.DataFrame(columns=["platform", "record_date"])
    assert summary(Snapshot(frame, {})) == {
        "info": {},
        "platform_counts": {},
        "month_counts": {},
        "unknown_dates": 0,
    }


@pytest.mark.parametrize("missing", ["platform", "record_date"])
def test_summary_does_not_hide_missing_columns(missing):
    frame = pd.DataFrame({"platform": ["app_store"], "record_date": [None]})
    with pytest.raises((KeyError, AttributeError)):
        summary(Snapshot(frame.drop(columns=[missing]), {}))
