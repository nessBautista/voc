"""The selection is validated once, written canonically, and never formatted into SQL."""

import pytest

from voc.publications.selection import (
    FIELDS,
    SelectionError,
    canonical_selection,
    like_pattern,
)


def test_defaults_are_explicit_nulls_and_lists_are_canonical():
    assert set(canonical_selection(None, "summary")) == set(FIELDS)
    selection = canonical_selection(
        {"platforms": "x, google_play,x", "labels": ["positive", "negative"]}, "summary"
    )
    assert selection["platforms"] == ["google_play", "x"]
    assert selection["labels"] == ["negative", "positive"]
    # Naming every value is the same as naming none.
    assert (
        canonical_selection({"labels": "negative,neutral,positive"}, "summary")[
            "labels"
        ]
        is None
    )


def test_kind_parameters_and_their_defaults():
    assert canonical_selection({}, "topic_list")["sort"] == "volume"
    reviews = canonical_selection(
        {"page": "2", "page_size": "6", "topic_id": "-1"}, "reviews"
    )
    assert (reviews["page"], reviews["page_size"], reviews["topic_id"]) == (2, 6, -1)
    assert canonical_selection({}, "reviews")["page_size"] == 10


@pytest.mark.parametrize(
    "raw,kind,message",
    [
        ({"platforms": "facebook"}, "summary", "unknown values"),
        ({"platforms": ""}, "summary", "at least one"),
        ({"labels": []}, "summary", "at least one"),
        ({"month_from": "2026-13"}, "summary", "YYYY-MM"),
        ({"month_from": "2026-09", "month_to": "2026-01"}, "summary", "not be later"),
        ({"search": "x" * 101}, "reviews", "at most 100"),
        ({"search": "a\x00b"}, "reviews", "control characters"),
        ({"topic_id": "-2"}, "reviews", "at least -1"),
        ({"topic_id": True}, "reviews", "integer"),
        ({"page_size": 51}, "reviews", "between 1 and 50"),
        ({"page": 0}, "reviews", "at least 1"),
        ({"sort": "random"}, "topic_list", "volume, negative or positive"),
        ({"sort": "negative"}, "summary", "does not take sort"),
        ({"topic_id": 1}, "summary", "does not take topic_id"),
        ({}, "topic_detail", "requires topic_id"),
        ({"catalog": "/etc"}, "summary", "Unknown selection fields"),
        ({}, "overview", "takes no selection"),
        ([], "summary", "must be an object"),
    ],
)
def test_invalid_selections_are_refused_with_a_reason(raw, kind, message):
    with pytest.raises(SelectionError, match=message):
        canonical_selection(raw, kind)


def test_like_pattern_keeps_wildcards_literal():
    assert like_pattern("100%_x\\y") == "%100\\%\\_x\\\\y%"
