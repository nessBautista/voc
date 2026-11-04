"""Review gate and bounded development batches over the frozen requests."""

from decimal import Decimal
from time import monotonic
from uuid import uuid4

from .ledger import atomic_json
from .production import inspect_study, now

CONDITIONS = ("R0-P0", "R2-P0", "R2-P1", "R2-P2")


def development_plan(store):
    summary = inspect_study(store)
    state = store.load()
    protocol = state["protocol"]
    ids = [c["topic_id"] for c in protocol["cases"]]
    expected = {f"{tid}-{condition}" for tid in ids for condition in CONDITIONS}
    if (
        len(ids) != 12
        or len(set(ids)) != 12
        or set(protocol["requests"]) != expected
        or protocol["reserve_enabled"]
    ):
        raise ValueError(
            "Expected the frozen 12-case development protocol, without reserve execution"
        )
    if any(
        (a["state"] != "accepted" and k not in summary["failure_reviews"])
        or not a.get("tracking_verified")
        for k, a in summary["attempts"].items()
    ):
        raise ValueError(
            "Resolve incomplete, invalid, uncertain or unverified attempts first"
        )
    conditions = {}
    for condition in CONDITIONS:
        keys = [f"{tid}-{condition}" for tid in ids]
        complete = [
            k
            for k in keys
            if k in summary["attempts"]
            and summary["attempts"][k]["state"] == "accepted"
        ]
        reviewed = [k for k in keys if k in summary["failure_reviews"]]
        conditions[condition] = {
            "completed": complete,
            "reviewed_failures": reviewed,
            "pending": [k for k in keys if k not in summary["attempts"]],
        }
    pending = sum(len(c["pending"]) for c in conditions.values())
    allowance = Decimal(protocol["per_attempt_allowance_usd"]) * pending
    if len(summary["attempts"]) + pending > protocol["provider"]["max_attempts"]:
        raise ValueError("Insufficient attempt budget for development")
    if allowance > Decimal(summary["remaining_usd"]):
        raise ValueError("Insufficient cost budget for development")
    return {
        "protocol_sha256": state["protocol_sha256"],
        "development_enabled": state["development_enabled"],
        "reserve_enabled": False,
        "conditions": conditions,
        "completed": sum(len(c["completed"]) for c in conditions.values()),
        "reviewed_failures": list(summary["failure_reviews"]),
        "attempted": len(summary["attempts"]),
        "pending": pending,
        "remaining_allowance_usd": str(allowance),
        "accounted_usd": summary["accounted_or_reserved_usd"],
        "budget_usd": summary["budget_usd"],
        "model_requests": 0,
    }


def enable_development(store):
    """Authorize only the existing 48 requests after two verified integration cases."""
    with store.lock():
        development_plan(store)
        state = store.load()
        if any(
            k not in state["attempts"] or state["attempts"][k]["state"] != "accepted"
            for k in state["protocol"]["initial_requests"]
        ):
            raise ValueError(
                "Complete both initial integration cases before enabling development"
            )
        if not state["development_enabled"]:
            state["development_enabled"] = True
            state["development_gate"] = {
                "enabled_at": now(),
                "protocol_sha256": state["protocol_sha256"],
                "initial_attempts": {
                    k: state["attempts"][k]["attempt_id"]
                    for k in state["protocol"]["initial_requests"]
                },
                "semantic_review": "assisted initial review; comparative scoring remains pending",
            }
            store.save(state)
    return development_plan(store)


def run_development(condition, *, store, runner, clock=monotonic):
    """One condition, at most twelve pending requests, under a 900-second deadline.

    The runner owns a cancellable workflow process. It receives the remaining
    wall-time allowance. The operation never replays recorded failures.
    """
    if condition not in CONDITIONS:
        raise ValueError("Unknown development condition")
    with store.lock(name=".batch.lock"):
        started = clock()
        plan = development_plan(store)
        if not plan["development_enabled"]:
            raise ValueError("Enable development after the integration review")
        keys = plan["conditions"][condition]["pending"]
        if not keys:
            return {
                "state": (
                    "already_completed_with_failures"
                    if plan["conditions"][condition]["reviewed_failures"]
                    else "already_completed"
                ),
                "condition": condition,
                "new_attempts": 0,
                "completed": len(plan["conditions"][condition]["completed"]),
                "reviewed_failures": plan["conditions"][condition]["reviewed_failures"],
                "reserve_enabled": False,
            }
        batch_id = str(uuid4())
        folder = store.root / "batches" / batch_id
        folder.mkdir(parents=True, exist_ok=False)
        batch = {
            "batch_id": batch_id,
            "protocol_sha256": plan["protocol_sha256"],
            "condition": condition,
            "state": "running",
            "started_at": now(),
            "planned": keys,
            "completed_now": [],
            "reused": plan["conditions"][condition]["completed"],
            "reviewed_failures": plan["conditions"][condition]["reviewed_failures"],
            "deadline_seconds": 900,
            "per_workflow_seconds": 120,
            "reserve_enabled": False,
        }
        atomic_json(folder / "batch.json", batch)
        for key in keys:
            remaining = 900 - (clock() - started)
            if remaining <= 1:
                batch.update(state="stopped", reason="batch_deadline")
                break
            try:
                current = development_plan(store)
                if key in current["conditions"][condition]["completed"]:
                    batch["reused"].append(key)
                    continue
                runner(
                    key,
                    store=store,
                    seconds=min(120, remaining),
                    log_path=folder / (key + ".log"),
                )
                # Check durable receipts, not the process exit code alone.
                current = development_plan(store)
                if key not in current["conditions"][condition]["completed"]:
                    raise ValueError("Workflow has no verified completed receipt")
                batch["completed_now"].append(key)
                atomic_json(folder / "batch.json", batch)
            except (Exception, KeyboardInterrupt):  # noqa: BLE001 - no provider or tracking exception text in receipts
                batch.update(
                    state="stopped",
                    reason="workflow_requires_inspection",
                    stopped_at_key=key,
                )
                break
        else:
            batch["state"] = (
                "completed_with_failures" if batch["reviewed_failures"] else "completed"
            )
        batch.update(finished_at=now(), wall_seconds=clock() - started)
        atomic_json(folder / "batch.json", batch)
        return batch
