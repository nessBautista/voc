"""Verify every published review against saved candidates; requires the local app."""

import json
import subprocess
import urllib.request
from pathlib import Path

import pandas as pd

from voc.analysis.store import read_analysis
from voc_ml.experiments.operations import load_protocol

ROOT = Path(__file__).resolve().parents[1]
root = "http://127.0.0.1:8092"
experiment = ROOT / "data/experiments/julaug-2026"
protocol = load_protocol(experiment)
results = {}
sets = {}


def get(path):
    return json.load(urllib.request.urlopen(root + path, timeout=20))


assert get("/health/ready")["status"] == "ready"
try:
    for system in ["base", "alternative"]:
        subprocess.run(
            ["bash", str(ROOT / "scripts/difusion-demo.sh"), "select", system],
            check=True,
            capture_output=True,
        )
        selection = get("/api/v1/publications/current")
        pub = selection["publication_id"]
        receipt = json.loads((experiment / f"{system}-run.json").read_text())
        stored = read_analysis(receipt["representation"]["analysis_id"])
        expected = stored.reviews.set_index("record_id")["model_text"].to_dict()
        assignments = {a.record_id: a.topic_id for a in stored.assignments}
        sentiments = {s.record_id: s.model_label for s in stored.sentiments}
        api = f"/api/v1/publications/{pub}"
        summary = get(api + "/summary")["data"]
        assert summary["reviews_total"] == len(expected)
        items = []
        page = 1
        while True:
            data = get(api + f"/reviews?page={page}&page_size=50")["data"]
            items += data["items"]
            if page >= data["pages"]:
                break
            page += 1
        assert len(items) == len(expected) == len({x["record_id"] for x in items})
        assert {x["record_id"] for x in items} == set(expected)
        for item in items:
            rid = item["record_id"]
            assert item["text"] == expected[rid]
            assert item["topic_id"] == assignments[rid]
            assert item["label"] == sentiments[rid]
        sets[system] = set(expected)
        topics = get(api + "/topic_list")["data"]
        assert sum(t["member_count"] for t in topics) == len(expected)
        topic0 = get(api + "/topic_detail?topic_id=0")["data"]
        negative = get(api + "/summary?labels=negative")["data"]
        assert negative["reviews_in_view"] == sum(
            s == "negative" for s in sentiments.values()
        )
        results[system] = {
            "publication_id": pub,
            "analysis_id": stored.manifest.analysis_id,
            "reviews": len(items),
            "topics": summary["topics_total"],
            "sentiment": {
                k: summary[k]["count"] for k in ["negative", "neutral", "positive"]
            },
            "all_texts_assignments_sentiment_match": True,
            "unassigned": sum(v == -1 for v in assignments.values()),
            "topic_zero_members": topic0["member_count"],
        }
finally:
    subprocess.run(
        ["bash", str(ROOT / "scripts/difusion-demo.sh"), "select", "alternative"],
        check=True,
        capture_output=True,
    )

relevance = pd.read_csv(experiment / "relevance.csv")

removed = sets["base"] - sets["alternative"]
assert removed == set(relevance.loc[~relevance["keep"], "record_id"])
assert len(removed) == 13 and not sets["alternative"] - sets["base"]
# All 13 removed records remain visible in the base publication.
results["excluded_record_ids"] = sorted(removed)
results["protocol_id"] = protocol["protocol_id"]
results["model_requests"] = 0
out = ROOT / "data/reports/difusion/demo"
out.mkdir(parents=True, exist_ok=True)
(out / "api-verification.json").write_text(
    json.dumps(results, indent=2, ensure_ascii=False) + "\n"
)
print(
    json.dumps(
        {k: v for k, v in results.items() if k != "excluded_record_ids"}, indent=2
    )
)
