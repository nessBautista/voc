"""Frozen P0 holdout evaluation, reusing the bounded interpretation producer."""

from copy import deepcopy
from decimal import Decimal
from time import monotonic
from uuid import uuid4

from voc.embeddings.shared.contracts import fingerprint
from voc.topics.interpretation import validate_case

from .development import development_plan
from .execution_plan import request_body, reserve_usd
from .ledger import atomic_json
from .production import inspect_study, now
from .prompts import Generation, Prompt


def parent_identity(parent):
    plan = development_plan(parent)
    if plan["pending"] or plan["attempted"] != 48:
        raise ValueError("Complete all 48 development attempts before opening reserve")
    state = parent.load()
    return {
        "protocol_sha256": state["protocol_sha256"],
        "attempts_sha256": fingerprint(state["attempts"]),
        "accounted_usd": plan["accounted_usd"],
        "attempted": plan["attempted"],
    }


def create_reserve(*, parent, store, draft, decision):
    """Open the original reserve only after a recorded P0 decision. No model call."""
    if store.root != parent.root.parent / "reserve-execution":
        raise ValueError("Use the single reserve-execution store beside development")
    with parent.lock(name=".batch.lock"), parent.lock():
        identity = parent_identity(parent)
        original = parent.load()["protocol"]
        plan = draft["plan"]
        if (
            fingerprint(plan) != draft["plan_sha256"]
            or draft["plan_sha256"] != original["draft_sha256"]
            or plan["input"] != original["input"]
            or fingerprint(plan["cases"]["reserve"]) != original["reserve_cases_sha256"]
        ):
            raise ValueError("Reserve differs from the frozen development proposal")
        if (
            decision.get("prompt") != "P0"
            or decision.get("development_protocol_sha256")
            != identity["protocol_sha256"]
            or any(
                not isinstance(decision.get(k), str) or not decision[k].strip()
                for k in ("selected_by", "selected_at", "rationale")
            )
        ):
            raise ValueError(
                "Record the P0 selection and its authorship before reserve"
            )
        cases = deepcopy(plan["cases"]["reserve"])
        ids = [c["topic_id"] for c in cases]
        if (
            len(ids) != 12
            or len(set(ids)) != 12
            or set(ids) & {c["topic_id"] for c in original["cases"]}
        ):
            raise ValueError(
                "Expected twelve distinct reserved topics outside development"
            )
        for case in cases:
            validate_case(case)
        spec = plan["prompts"]["P0"]
        prompt = Prompt("P0", spec["version"], spec["text"])
        if prompt.sha256 != spec["sha256"] or any(
            r["body"]["messages"][0]["content"] != prompt.text
            or r["prompt_sha256"] != prompt.sha256
            for r in original["requests"].values()
            if r["prompt"] == "P0"
        ):
            raise ValueError("Selected P0 differs from development")
        provider = deepcopy(original["provider"])
        allowance = reserve_usd(provider) * 12
        if (
            Decimal(identity["accounted_usd"]) + allowance
            > Decimal(provider["max_cost_usd"])
            or identity["attempted"] + 12 > provider["max_attempts"]
        ):
            raise ValueError("Reserve exceeds remaining shared study budget")
        # A separately capped allocation cannot reset the original study allowance.
        provider.update(max_attempts=12, max_cost_usd=str(allowance))
        requests = {
            f"{case['topic_id']}-R2-P0": request_body(
                case, "R2", prompt, Generation(**plan["generation"]), provider
            )
            for case in cases
        }
        protocol = {
            "schema_version": "voc-interpretation-protocol-v1",
            "phase": "reserve",
            "parent": identity,
            "decision": deepcopy(decision),
            "draft_sha256": draft["plan_sha256"],
            "input": deepcopy(original["input"]),
            "cases": cases,
            "reserve_cases_sha256": original["reserve_cases_sha256"],
            "provider": provider,
            "requests": requests,
            "initial_requests": list(requests),
            "reserve_enabled": True,
            "per_attempt_allowance_usd": str(reserve_usd(provider)),
            "max_study_allowance_usd": str(allowance),
            "review_policy": original["review_policy"],
            "acceptance": {
                "planned_cases": 12,
                "max_critical_errors": 0,
                "minimum_cases_all_criteria_ge_one": 10,
            },
        }
        store.initialize(
            protocol, {"source": "development catalog; rechecked before each send"}
        )
    return reserve_plan(parent=parent, store=store)


def reserve_plan(*, parent, store):
    identity = parent_identity(parent)
    state = store.load()
    protocol = state["protocol"]
    if protocol.get("phase") != "reserve" or protocol.get("parent") != identity:
        raise ValueError("Reserve parent identity differs")
    summary = inspect_study(store)
    if any(
        (a["state"] != "accepted" and k not in summary["failure_reviews"])
        or not a.get("tracking_verified")
        for k, a in summary["attempts"].items()
    ):
        raise ValueError(
            "Inspect and resolve failed, uncertain or unverified reserve attempts"
        )
    completed = [k for k, a in summary["attempts"].items() if a["state"] == "accepted"]
    pending = [k for k in protocol["requests"] if k not in summary["attempts"]]
    return {
        "protocol_sha256": state["protocol_sha256"],
        "parent_protocol_sha256": identity["protocol_sha256"],
        "phase": "reserve",
        "condition": "R2-P0",
        "planned_cases": 12,
        "completed": completed,
        "reviewed_failures": list(summary["failure_reviews"]),
        "pending": pending,
        "accounted_usd": summary["accounted_or_reserved_usd"],
        "combined_accounted_usd": str(
            Decimal(identity["accounted_usd"])
            + Decimal(summary["accounted_or_reserved_usd"])
        ),
        "remaining_allowance_usd": str(
            Decimal(protocol["per_attempt_allowance_usd"]) * len(pending)
        ),
        "allocated_usd": protocol["provider"]["max_cost_usd"],
        "reserve_enabled": True,
        "model_requests": 0,
    }


def run_reserve(*, parent, store, runner, clock=monotonic):
    """At most twelve new calls, bounded workflows, no automatic retry."""
    with parent.lock(name=".batch.lock"), store.lock(name=".batch.lock"):
        started = clock()
        plan = reserve_plan(parent=parent, store=store)
        if not plan["pending"]:
            return {
                **plan,
                "state": "already_completed_with_failures"
                if plan["reviewed_failures"]
                else "already_completed",
            }
        folder = store.root / "batches" / str(uuid4())
        folder.mkdir(parents=True, exist_ok=False)
        batch = {
            "protocol_sha256": plan["protocol_sha256"],
            "phase": "reserve",
            "state": "running",
            "started_at": now(),
            "planned": plan["pending"],
            "completed_now": [],
            "reused": plan["completed"],
            "reviewed_failures": plan["reviewed_failures"],
            "deadline_seconds": 900,
            "per_workflow_seconds": 120,
        }
        atomic_json(folder / "batch.json", batch)
        for key in plan["pending"]:
            remaining = 900 - (clock() - started)
            if remaining <= 1:
                batch.update(state="stopped", reason="batch_deadline")
                break
            try:
                reserve_plan(parent=parent, store=store)
                runner(
                    key,
                    store=store,
                    seconds=min(120, remaining),
                    log_path=folder / (key + ".log"),
                )
                current = reserve_plan(parent=parent, store=store)
                if key not in current["completed"]:
                    raise ValueError("Workflow has no verified receipt")
                batch["completed_now"].append(key)
                atomic_json(folder / "batch.json", batch)
            except (Exception, KeyboardInterrupt):  # noqa: BLE001 - omit private provider exceptions
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
