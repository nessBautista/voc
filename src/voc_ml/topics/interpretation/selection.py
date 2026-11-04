"""Reproducible development/reserve proposals with an explicit leakage audit."""

import hashlib
import re
import unicodedata
from collections import Counter


def normalized(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(
        re.findall(r"\w+", "".join(c for c in text if not unicodedata.combining(c)))
    )


def grams(text):
    return frozenset(text[i : i + 3] for i in range(len(text) - 2))


def band(size):
    return "small" if size < 20 else "medium" if size < 50 else "large"


def propose_partitions(bundle, known_topics, exposed_ids, *, seed=42):
    """12 development (six known plus two per size band), 12 reserve (four/band).

    Reserve topics are rejected when ANY member overlaps known exposure or the
    development members. Near-duplicate screening uses normalized character
    trigram Jaccard >= .85, not a claim of semantic independence.
    """
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Invalid selection seed")
    if (
        len(known_topics) != 6
        or len(set(known_topics)) != 6
        or any(type(t) is not int or t < 0 for t in known_topics)
    ):
        raise ValueError("Provide six distinct already exposed development topics")
    topics = {t["topic_id"]: t for t in bundle.topics if t["topic_id"] >= 0}
    if not set(known_topics) <= topics.keys():
        raise ValueError("Known topic missing from grouping")
    text = bundle.reviews.set_index("record_id").model_text.to_dict()
    if not set(exposed_ids) <= text.keys():
        raise ValueError("Exposure contains records outside this population")
    members = bundle.assignments.groupby("topic_id").record_id.agg(list).to_dict()

    def rank(tid, phase):
        return hashlib.sha256(f"{seed}:{phase}:{tid}".encode()).hexdigest()

    development = list(known_topics)
    for name in ("small", "medium", "large"):
        candidates = sorted(
            (
                t
                for t in topics
                if t not in development and band(topics[t]["size"]) == name
            ),
            key=lambda t: rank(t, "development"),
        )
        if len(candidates) < 2:
            raise ValueError("Insufficient development candidates in a size band")
        development.extend(candidates[:2])
    blocked_ids = set(exposed_ids)
    for tid in development:
        blocked_ids.update(members[tid])
    blocked_texts = {normalized(text[rid]) for rid in blocked_ids}
    blocked_grams = [grams(t) for t in sorted(blocked_texts) if len(t) >= 12]
    excluded = Counter()
    reserve = []
    for name in ("small", "medium", "large"):
        taken = 0
        candidates = sorted(
            (
                t
                for t in topics
                if t not in development and band(topics[t]["size"]) == name
            ),
            key=lambda t: rank(t, "reserve"),
        )
        for tid in candidates:
            ids = members[tid]
            if blocked_ids.intersection(ids):
                excluded["record_overlap"] += 1
                continue
            candidate_texts = {normalized(text[rid]) for rid in ids}
            if candidate_texts & blocked_texts:
                excluded["normalized_text_overlap"] += 1
                continue
            candidate_grams = [grams(t) for t in candidate_texts if len(t) >= 12]
            collision = False
            for left in candidate_grams:
                for right in blocked_grams:
                    if min(len(left), len(right)) < 0.85 * max(len(left), len(right)):
                        continue
                    if len(left & right) / len(left | right) >= 0.85:
                        collision = True
                        break
                if collision:
                    break
            if collision:
                excluded["near_duplicate_overlap"] += 1
                continue
            reserve.append(tid)
            # Also avoid repeated text across the chosen reserve topics.
            blocked_ids.update(ids)
            blocked_texts.update(candidate_texts)
            blocked_grams.extend(candidate_grams)
            taken += 1
            if taken == 4:
                break
        if taken != 4:
            raise ValueError(
                "Insufficient clean reserve cases; do not relax leakage rules silently"
            )
    return {
        "development": development,
        "reserve": reserve,
        "audit": {
            "policy": "six-known-two-per-band-four-reserve-v1",
            "seed": seed,
            "known_exposed_topics": list(known_topics),
            "known_exposed_records": len(exposed_ids),
            "screened_members": "all members of candidate topics",
            "duplicate_policy": "NFKD-casefold-word-space-exact-and-char3-jaccard-ge-0.85-v1",
            "near_duplicate_min_characters": 12,
            "rejected_candidates": dict(excluded),
            "reserve_size_bands": {
                name: sum(band(topics[t]["size"]) == name for t in reserve)
                for name in ("small", "medium", "large")
            },
            "limitations": [
                "Only registered exposure is known; unsaved prior viewing is not detected.",
                "Size bands do not guarantee semantic category coverage.",
                "String overlap screening does not establish semantic independence.",
                "The grouping was fitted on the full corpus, including reserve records.",
            ],
        },
    }
