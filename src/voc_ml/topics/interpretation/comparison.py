"""Read-only development comparison and review templates over verified receipts."""

from copy import deepcopy
from decimal import Decimal
from random import Random

from voc.embeddings.shared.contracts import fingerprint
from voc.topics.interpretation_results import read_interpretation

from .development import CONDITIONS, development_plan
from .production import inspect_study

CRITERIA = (
    "supported_feature",
    "faithful_behavior",
    "usefulness",
    "ambiguity_and_citations",
)


def read_development_comparison(*, store, protocol_sha256):
    """Require complete development, retain failures, never open reserve or invoke models."""
    state = store.load()
    if state["protocol_sha256"] != protocol_sha256:
        raise ValueError("Unexpected development protocol")
    plan = development_plan(store)
    if plan["pending"]:
        raise ValueError("Development requests remain pending")
    return _read_results(store, state, CONDITIONS, "development")


def read_reserve_results(*, parent, store, protocol_sha256):
    """Read completed holdout receipts with the same evidence and scoring contract."""
    from .reserve import reserve_plan

    state = store.load()
    if state["protocol_sha256"] != protocol_sha256:
        raise ValueError("Unexpected reserve protocol")
    if reserve_plan(parent=parent, store=store)["pending"]:
        raise ValueError("Reserve requests remain pending")
    return _read_results(store, state, ("R2-P0",), "reserve")


def _read_results(store, state, conditions, phase):
    protocol_sha256 = state["protocol_sha256"]
    summary = inspect_study(store)
    protocol = state["protocol"]
    cases = {case["topic_id"]: case for case in protocol["cases"]}
    rows, references, prompts = [], {}, {}
    for tid, case in cases.items():
        for condition in conditions:
            key = f"{tid}-{condition}"
            attempt = summary["attempts"][key]
            receipt = read_interpretation(
                store.root / attempt["release"],
                manifest_sha256=attempt["manifest_sha256"],
            )
            request = receipt["request"]
            if (
                receipt["case"] != case
                or request != protocol["requests"][key]
                or receipt["provider"] != protocol["provider"]
                or receipt["source_input"] != protocol["input"]
            ):
                raise ValueError("Receipt differs from frozen case or request")
            prompt = {
                "version": request["prompt_version"],
                "sha256": request["prompt_sha256"],
                "text": request["body"]["messages"][0]["content"],
            }
            if request["prompt"] in prompts and prompts[request["prompt"]] != prompt:
                raise ValueError("Prompt changed between cases")
            prompts[request["prompt"]] = prompt
            reference = {
                "attempt_id": attempt["attempt_id"],
                "manifest_sha256": attempt["manifest_sha256"],
                "request_sha256": attempt["request_sha256"],
            }
            references[key] = reference
            desc = receipt["outcome"]["description"] or {}
            response = receipt["response"]
            choice = response["choices"][0]
            labels = {e["record_id"]: e["label"] for e in case["evidence"]}
            rows.append(
                {
                    "request_key": key,
                    "topic_id": tid,
                    "condition": condition,
                    "state": attempt["state"],
                    "failure_reviewed": key in summary["failure_reviews"],
                    "error": receipt["outcome"]["error"],
                    "finish_reason": choice["finish_reason"],
                    "assessment": desc.get("assessment"),
                    "name": desc.get("name"),
                    "description": desc.get("topic_description"),
                    "proposed_feature": desc.get("proposed_feature"),
                    "supporting_evidence": [
                        labels[rid] for rid in desc.get("supporting_review_ids", [])
                    ],
                    "raw_content": choice["message"].get("content"),
                    "prompt_tokens": response["usage"]["prompt_tokens"],
                    "completion_tokens": response["usage"]["completion_tokens"],
                    "cost_usd": attempt["charged_usd"],
                    "wall_seconds": receipt["wall_seconds"],
                    "tracking_verified": attempt["tracking_verified"],
                    "tracking": receipt["tracking"],
                    **reference,
                }
            )
    totals = []
    for condition in conditions:
        selected = [r for r in rows if r["condition"] == condition]
        accepted = sum(r["state"] == "accepted" for r in selected)
        totals.append(
            {
                "condition": condition,
                "attempts": len(selected),
                "accepted": accepted,
                "invalid": len(selected) - accepted,
                "acceptance_rate": accepted / len(selected),
                "cost_usd": str(sum(Decimal(r["cost_usd"]) for r in selected)),
                "prompt_tokens": sum(r["prompt_tokens"] for r in selected),
                "completion_tokens": sum(r["completion_tokens"] for r in selected),
            }
        )
    pair_coverage = []
    contrasts = (
        (("R0-P0", "R2-P0"), ("R2-P0", "R2-P1"), ("R2-P0", "R2-P2"))
        if phase == "development"
        else ()
    )
    for left, right in contrasts:
        valid = {
            c: {
                r["topic_id"]
                for r in rows
                if r["condition"] == c and r["state"] == "accepted"
            }
            for c in (left, right)
        }
        pair_coverage.append(
            {
                "left": left,
                "right": right,
                "planned_pairs": len(cases),
                "available_pairs": len(valid[left] & valid[right]),
                "missing_topic_ids": [
                    tid for tid in cases if tid not in valid[left] & valid[right]
                ],
            }
        )
    if fingerprint(store.load()) != fingerprint(state):
        raise ValueError("Ledger changed during comparison")
    identity = {"protocol_sha256": protocol_sha256, "receipts": references}
    return {
        "schema_version": f"voc-interpretation-{phase}-comparison-v1",
        "source_sha256": fingerprint(identity),
        **identity,
        "cases": list(cases.values()),
        "prompts": prompts,
        "provider": protocol["provider"],
        "source_input": protocol["input"],
        "rows": rows,
        "summaries": totals,
        "pair_coverage": pair_coverage,
        "failure_reviews": summary["failure_reviews"],
        "accounted_usd": summary["accounted_or_reserved_usd"],
        "reserve_opened": phase == "reserve",
        "model_requests": 0,
    }


def review_template(comparison, *, seed=42):
    """Neutral labels are randomized per case, with a stored and reproducible key."""
    rng = Random(seed)
    lookup = {r["request_key"]: r for r in comparison["rows"]}
    entries = []
    for case in comparison["cases"]:
        conditions = (
            ["R2-P0"]
            if comparison["schema_version"]
            == "voc-interpretation-reserve-comparison-v1"
            else list(CONDITIONS)
        )
        rng.shuffle(conditions)
        for label, condition in zip("ABCD"[: len(conditions)], conditions, strict=True):
            key = f"{case['topic_id']}-{condition}"
            entries.append(
                {
                    "topic_id": case["topic_id"],
                    "label": label,
                    "request_key": key,
                    "status": "pending"
                    if lookup[key]["state"] == "accepted"
                    else "unscorable",
                    "scores": dict.fromkeys(CRITERIA),
                    "critical_error": None,
                    "evidence_labels": [],
                    "note": "",
                    "reviewer": "",
                    "review_kind": None,
                    "reviewed_at": None,
                }
            )
    return {
        "schema_version": "voc-interpretation-review-v1",
        "source_sha256": comparison["source_sha256"],
        "protocol_sha256": comparison["protocol_sha256"],
        "label_seed": seed,
        "entries": entries,
    }


def validate_review(review, comparison):
    expected = review_template(comparison, seed=review.get("label_seed", 42))
    if any(
        review.get(k) != expected[k]
        for k in ("schema_version", "source_sha256", "protocol_sha256", "label_seed")
    ) or len(review["entries"]) != len(expected["entries"]):
        raise ValueError("Review source or size differs")
    cases = {c["topic_id"]: c for c in comparison["cases"]}
    for entry, base in zip(review["entries"], expected["entries"], strict=True):
        if any(entry[k] != base[k] for k in ("topic_id", "label", "request_key")):
            raise ValueError("Review label mapping differs")
        if set(entry["scores"]) != set(CRITERIA):
            raise ValueError("Review criteria differ")
        status = entry["status"]
        if base["status"] == "unscorable":
            if entry != base:
                raise ValueError("Generation failure must remain unscorable")
            continue
        if status == "pending":
            if entry != base:
                raise ValueError("Complete a review before recording scores")
            continue
        if status != "scored" or any(
            type(v) is not int or v not in (0, 1, 2) for v in entry["scores"].values()
        ):
            raise ValueError("Scores must be integers 0, 1 or 2")
        if (
            type(entry["critical_error"]) is not bool
            or not isinstance(entry["reviewer"], str)
            or not entry["reviewer"].strip()
            or entry["review_kind"] not in ("human", "assisted")
            or not isinstance(entry["reviewed_at"], str)
            or not entry["reviewed_at"]
            or not isinstance(entry["note"], str)
            or not entry["note"].strip()
        ):
            raise ValueError("Review authorship, date and explanation required")
        labels = entry["evidence_labels"]
        allowed = {e["label"] for e in cases[entry["topic_id"]]["evidence"]}
        if not labels or len(set(labels)) != len(labels) or not set(labels) <= allowed:
            raise ValueError("Review must cite supplied evidence labels")
    return review


def apply_review_updates(review, updates, comparison):
    """Apply explicit reviewer entries. Empty updates never create scores."""
    result = deepcopy(review)
    validate_review(result, comparison)
    for update in updates:
        entry = next(
            (
                e
                for e in result["entries"]
                if (e["topic_id"], e["label"]) == (update["topic_id"], update["label"])
            ),
            None,
        )
        if entry is None or entry["status"] == "unscorable":
            raise ValueError("Unknown or unscorable review entry")
        allowed = {
            "topic_id",
            "label",
            "scores",
            "critical_error",
            "evidence_labels",
            "note",
            "reviewer",
            "review_kind",
            "reviewed_at",
        }
        if set(update) != allowed:
            raise ValueError("Provide a complete review update")
        entry.update(
            {k: v for k, v in update.items() if k not in ("topic_id", "label")}
        )
        entry["status"] = "scored"
    return validate_review(result, comparison)


def evaluate_reserve(review, comparison):
    """Apply fixed descriptive criteria, preserving reviewer kind and missing scores."""
    if comparison["schema_version"] != "voc-interpretation-reserve-comparison-v1":
        raise ValueError("Expected reserved evaluation results")
    validate_review(review, comparison)
    entries = review["entries"]
    if len(entries) != 12:
        raise ValueError("Expected twelve reserved cases")
    scored = [e for e in entries if e["status"] == "scored"]
    kinds = {e["review_kind"] for e in scored}
    if len(kinds) > 1:
        raise ValueError("Keep human and assisted reserve evaluations separate")
    pending = sum(e["status"] == "pending" for e in entries)
    errors = sum(e["critical_error"] for e in scored)
    qualified = sum(min(e["scores"].values()) >= 1 for e in scored)
    kind = next(iter(kinds), None)
    passed = not pending and errors == 0 and qualified >= 10
    return {
        "protocol_sha256": comparison["protocol_sha256"],
        "source_sha256": comparison["source_sha256"],
        "review_sha256": fingerprint(review),
        "state": "review_pending"
        if pending
        else ("criteria_met" if passed else "criteria_not_met"),
        "review_kind": kind,
        "planned_cases": 12,
        "scored_cases": len(scored),
        "pending_cases": pending,
        "generation_failures": sum(e["status"] == "unscorable" for e in entries),
        "critical_errors": errors,
        "cases_all_criteria_ge_one": qualified,
        "human_validation_pending": kind != "human" or bool(pending),
        "automatic_promotion": False,
    }
