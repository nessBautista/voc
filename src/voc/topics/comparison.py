"""Read-only comparisons of verified topic artifacts, without model imports."""

from collections import Counter
from math import comb

import numpy as np


def assert_same_input(left, right):
    if left.info["input"] != right.info["input"]:
        raise ValueError("Comparisons require identical dataset, sample and embeddings")
    if not left.reviews.equals(right.reviews):
        raise ValueError("Selected reviews differ")


def adjusted_rand(left, right):
    """ARI from a contingency table. Labels have no cross-run identity."""
    if len(left) != len(right):
        raise ValueError("Partition lengths differ")
    if len(left) < 2:
        return None
    pairs = sum(comb(n, 2) for n in Counter(zip(left, right)).values())
    a = sum(comb(n, 2) for n in Counter(left).values())
    b = sum(comb(n, 2) for n in Counter(right).values())
    expected = a * b / comb(len(left), 2)
    denominator = (a + b) / 2 - expected
    return 1.0 if denominator == 0 else (pairs - expected) / denominator


def compare_assignments(left, right):
    assert_same_input(left, right)
    a = left.assignments.set_index("record_id").topic_id
    b = right.assignments.set_index("record_id").topic_id.reindex(a.index)
    common = (a >= 0) & (b >= 0)
    return {
        "rows": len(a),
        "ari_including_unassigned": adjusted_rand(a.tolist(), b.tolist()),
        "ari_assigned_intersection": adjusted_rand(
            a[common].tolist(), b[common].tolist()
        ),
        "assigned_intersection_rows": int(common.sum()),
        "became_assigned": int(((a < 0) & (b >= 0)).sum()),
        "became_unassigned": int(((a >= 0) & (b < 0)).sum()),
        "unassigned_policy": "-1 is one group in the first ARI, excluded from the second",
    }


def summarize(bundle):
    sizes = sorted(t["size"] for t in bundle.topics if t["topic_id"] >= 0)
    execution = bundle.execution
    return {
        "run_id": bundle.info["run_id"],
        **bundle.info["counts"],
        "topic_sizes": sizes,
        "minimum_topic_size": min(sizes) if sizes else None,
        "median_topic_size": float(np.median(sizes)) if sizes else None,
        "maximum_topic_size": max(sizes) if sizes else None,
        "worker_wall_seconds": execution.get("worker_wall_seconds"),
        "peak_worker_rss_bytes": execution.get("fit", {}).get("peak_worker_rss_bytes"),
    }


def review_sample(bundle, *, seed=42):
    """Three largest groups, up to three seeded others and five unassigned rows."""
    ordered = sorted(
        (t for t in bundle.topics if t["topic_id"] >= 0),
        key=lambda t: (-t["size"], t["topic_id"]),
    )
    chosen = ordered[:3]
    rng = np.random.default_rng(seed)
    rest = ordered[3:]
    if rest:
        chosen += [
            rest[int(i)]
            for i in rng.choice(len(rest), min(3, len(rest)), replace=False)
        ]
    examples = [
        {**bundle.topic(t["topic_id"]), "reviewer": None, "scores": None, "note": None}
        for t in chosen
    ]
    for example in examples:
        example["evidence"] = example["evidence"][:3]
    outliers = bundle.assignments.loc[
        bundle.assignments.topic_id.eq(-1), "record_id"
    ].tolist()
    ids = (
        sorted(rng.choice(outliers, min(5, len(outliers)), replace=False).tolist())
        if outliers
        else []
    )
    return {
        "seed": seed,
        "topics": examples,
        "unassigned_ids": ids,
        "review_status": "pending",
        "scope": "exploratory examples, not all topic members",
    }
