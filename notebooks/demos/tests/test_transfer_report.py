import json

from voc_demo.sentiment.transfer_report import stores_v3_report


def test_published_report_matches_test_population_and_version():
    report = stores_v3_report()
    assert report["model"]["version"] == "v3"
    assert report["model"]["commit"] == "ff48c25f016499fc40d910f2a2a50072c706cdee"
    assert report["architecture"] == {
        "total_blocks": 12,
        "trained_blocks": 1,
        "frozen_blocks": 11,
    }
    assert (
        sum(sum(row) for row in report["confusion_matrix"])
        == report["data"]["partitions"]["test"]["rows"]
    )
    assert sum(row["support"] for row in report["classes"]) == 14438
    for i, row in enumerate(report["classes"]):
        assert sum(report["confusion_matrix"][i]) == row["support"]
    macro = next(row for row in report["comparison"] if row["key"] == "macro_f1")
    assert round(macro["v3"] - macro["original"], 4) == 0.1037
    assert len(json.dumps(report).encode()) < 1_000_000
    assert "/workspace/" not in json.dumps(report)
