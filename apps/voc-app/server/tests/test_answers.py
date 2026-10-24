"""The deterministic pieces of the analysis prose, checked on hand-written series."""

import pytest
from voc_app.agent.answers import (
    TREND_MINIMUM,
    counted,
    outside_ranking,
    pct,
    rises,
    three_parts,
)


def month(name, classified, negative):
    return {
        "month": name,
        "total": classified,
        "classified": classified,
        "negative": negative,
        "neutral": 0,
        "positive": classified - negative,
    }


def test_rises_name_comparable_months_only():
    series = [
        month("2026-01", 100, 20),
        month("2026-02", 100, 25),  # +5 points
        month("2026-03", TREND_MINIMUM - 1, 9),  # too thin to compare or to be a base
        month("2026-04", 100, 30),  # no base: the previous month was thin
        month("2026-05", 100, 30),  # flat
        month("2026-06", 200, 70),  # +5 points
        month("2026-07", 200, 60),  # a fall
    ]
    found = rises(series)
    assert [item["month"] for item in found] == ["2026-02", "2026-06"]
    assert found[0]["rise"] == pytest.approx(0.05)
    assert found[1]["share"] == pytest.approx(0.35)
    assert rises([]) == [] and rises([month("2026-01", 50, 10)]) == []
    # One point is the threshold; a hair under it is not a rise.
    assert rises([month("2026-01", 1000, 200), month("2026-02", 1000, 209)]) == []
    assert [
        r["month"]
        for r in rises([month("2026-01", 1000, 200), month("2026-02", 1000, 210)])
    ] == ["2026-02"]


def test_formatting_follows_the_tiles():
    assert pct(0.345) == "35%" and pct(0.344) == "34%" and pct(None) == "—"
    assert counted(1, "negativa") == "1 negativa"
    assert counted(1234, "negativa") == "1,234 negativas"
    text = three_parts("a", "b", "c")
    assert text.split("\n\n") == [
        "Qué dicen los datos. a",
        "Interpretación. b",
        "Lo que no se puede afirmar. c",
    ]


def test_outside_ranking_counts_left_out_topics_and_unassigned_reviews():
    def row(topic_id, assessment, classified, total=None):
        return {
            "topic_id": topic_id,
            "assessment": assessment,
            "classified": classified,
            "negative": classified // 2,
            "wilson_lower": 0.5 if topic_id >= 0 else None,
            "total": classified if total is None else total,
        }

    rows = [
        row(0, "single_feature", 10),
        row(1, "mixed", 4),
        row(2, "unclear", 30),
        row(3, "uninterpreted", 30),
        row(-1, "unassigned", 9, total=10),
    ]
    assert outside_ranking(rows) == (3, 10)


def test_specific_problems_rank_by_volume_and_skip_the_main_complaint():
    from voc_app.agent.answers import ranked_rows
    from voc_app.agent.contracts import Plan

    def row(topic_id, assessment, classified, negative, name):
        return {
            "topic_id": topic_id,
            "display_name": name,
            "assessment": assessment,
            "classified": classified,
            "negative": negative,
            "negative_share": negative / classified,
            "wilson_lower": 0.9 if negative / classified > 0.9 else 0.5,
            "total": classified,
            "keywords": [],
        }

    rows = [
        row(73, "single_feature", 264, 262, "Visualización"),  # the tile: skipped
        row(97, "single_feature", 175, 172, "Lentitud"),  # high share, small volume
        row(0, "single_feature", 2017, 1740, "Transferencias"),  # most negatives
        row(9, "single_feature", 1009, 971, "Estados de cuenta"),
        row(1, "mixed", 1956, 1712, "Acceso"),  # mixed: never specific
        row(50, "single_feature", 99, 99, "Pequeño"),  # under the minimum
    ]
    results = [
        {"kind": "summary", "data": {"most_negative_feature": {"topic_id": 73}}},
        {"kind": "topic_list", "data": rows},
    ]
    plan = Plan(
        intent="specific_problems",
        platform=None,
        selection=None,
        topic=None,
        limit=None,
    )
    assert [r["topic_id"] for r in ranked_rows(plan, results)] == [0, 9, 97]
    plan = Plan(intent="focus", platform=None, selection=None, topic=None, limit=2)
    assert [r["topic_id"] for r in ranked_rows(plan, results)] == [9, 73]


def test_scope_reads_a_single_month_as_one_month():
    from voc_app.agent.answers import scope
    from voc_app.agent.contracts import Selection

    one = Selection(
        platforms=None,
        labels=None,
        month_from="2026-08",
        month_to="2026-08",
        search=None,
    )
    assert scope(one) == "en todas las plataformas, en agosto de 2026"
    two = Selection(
        platforms=["x"],
        labels=None,
        month_from="2026-07",
        month_to="2026-08",
        search="tarjeta",
    )
    assert (
        scope(two)
        == "en X, entre julio de 2026 y agosto de 2026, que mencionan «tarjeta»"
    )
    assert scope(None) == "en todas las plataformas, en todo el periodo publicado"
