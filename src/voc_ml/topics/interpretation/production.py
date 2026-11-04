"""Bounded interpretation use case, with explicit transport and metadata ports."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from uuid import uuid4

from voc.embeddings.shared.contracts import canonical_json, fingerprint
from voc.topics.interpretation_results import assess_response, read_interpretation

from .failure_review import require_reviewable, review_identity, validate_reviews
from .ledger import atomic_json


def now():
    return datetime.now(UTC).isoformat()


def inspect_study(store):
    state = store.load()
    receipts = {}
    for key, attempt in state["attempts"].items():
        if attempt.get("release"):
            result = read_interpretation(
                store.root / attempt["release"],
                manifest_sha256=attempt["manifest_sha256"],
            )
            if (
                result["protocol_sha256"] != state["protocol_sha256"]
                or fingerprint(result["request"]) != attempt["request_sha256"]
                or result["outcome"] != attempt["outcome"]
                or result["attempt_id"] != attempt["attempt_id"]
                or result["tracking"] != attempt.get("tracking", {})
            ):
                raise ValueError("Ledger and receipt differ")
            cost = result["outcome"]["cost_usd"]
            expected = (
                Decimal(result["allowance_usd"])
                if result["outcome"]["state"] == "uncertain"
                else Decimal(cost)
            )
            if cost is not None:
                expected = max(expected, Decimal(cost))
            if (
                Decimal(attempt["charged_usd"]) != expected
                or attempt["state"] != result["outcome"]["state"]
            ):
                raise ValueError("Ledger accounting differs from verified receipt")
            receipts[key] = result
    validate_reviews(state, receipts)
    charged = sum(
        Decimal(a.get("charged_usd", a["allowance_usd"]))
        for a in state["attempts"].values()
    )
    return {
        "protocol_sha256": state["protocol_sha256"],
        "attempts": state["attempts"],
        "accounted_or_reserved_usd": str(charged),
        "budget_usd": state["protocol"]["provider"]["max_cost_usd"],
        "remaining_usd": str(
            Decimal(state["protocol"]["provider"]["max_cost_usd"]) - charged
        ),
        "failure_reviews": state.get("failure_reviews", {}),
        "development_enabled": state["development_enabled"],
        "reserve_enabled": state["protocol"].get("reserve_enabled", False),
    }


def execute_request(key, *, store, transport, catalog_reader, tracking=None):
    """Persist reservation before transport. Failures and uncertainty never replay."""
    with store.lock():
        state = store.load()
        inspect_study(store)  # Verify cached receipts before any new request.
        protocol = state["protocol"]
        if key not in protocol["requests"]:
            raise ValueError("Request outside frozen interpretation protocol")
        if key in state["attempts"]:
            prior = state["attempts"][key]
            if prior["state"] != "accepted":
                raise ValueError(
                    "Attempt already recorded; no replay of failed or uncertain sends"
                )
            return {"origin": "cache", **prior}
        if not state["development_enabled"] and key not in protocol["initial_requests"]:
            raise ValueError("Only the two initial requests are enabled")
        if any(
            (a["state"] != "accepted" and k not in state.get("failure_reviews", {}))
            or (a.get("tracking") and not a.get("tracking_verified"))
            for k, a in state["attempts"].items()
        ):
            raise ValueError(
                "Resolve the incomplete, invalid or uncertain attempt before continuing"
            )
        policy = protocol["provider"]
        if len(state["attempts"]) >= policy["max_attempts"]:
            raise ValueError("Attempt budget exhausted")
        allowance = Decimal(protocol["per_attempt_allowance_usd"])
        used = sum(Decimal(a["charged_usd"]) for a in state["attempts"].values())
        if used + allowance > Decimal(policy["max_cost_usd"]):
            raise ValueError("Cost budget exhausted")
        catalog = catalog_reader(policy)
        request = protocol["requests"][key]
        case = next(
            c for c in protocol["cases"] if c["topic_id"] == request["topic_id"]
        )
        attempt_id = str(uuid4())
        entry = {
            "attempt_id": attempt_id,
            "state": "sending",
            "started_at": now(),
            "request_sha256": fingerprint(request),
            "allowance_usd": str(allowance),
            "charged_usd": str(allowance),
            "catalog": catalog,
            "tracking": tracking or {},
            "response_origin": "provider",
        }
        state["attempts"][key] = entry
        store.save(state)
        began = monotonic()
        try:
            body = request["body"]
            if protocol["schema_version"] == "voc-fixed-topic-interpretation-v1":
                body = json.loads(request["body_json"])
                if body != request["body"]:
                    raise ValueError("Ordered request differs")
            response = transport(body)
            canonical_json(response)
        except (Exception, KeyboardInterrupt):  # noqa: BLE001 - provider exceptions may contain secrets
            response = {"transport_error": "uncertain"}
        outcome = assess_response(response, case, policy, str(allowance))
        result = {
            "schema_version": (
                "voc-interpretation-result-v2"
                if protocol["schema_version"] == "voc-fixed-topic-interpretation-v1"
                else "voc-interpretation-result-v1"
            ),
            "protocol_sha256": state["protocol_sha256"],
            "source_input": protocol["input"],
            "case": case,
            "request": request,
            "provider": policy,
            "allowance_usd": str(allowance),
            "response": response,
            "outcome": outcome,
            "attempt_id": attempt_id,
            "tracking": tracking or {},
            "wall_seconds": monotonic() - began,
            "completed_at": now(),
        }
        directory = store.root / "attempts" / attempt_id
        directory.mkdir(parents=True, exist_ok=False)
        atomic_json(directory / "result.json", result)
        manifest = {
            "schema_version": "voc-interpretation-release-v1",
            "protocol_sha256": state["protocol_sha256"],
            "result_sha256": fingerprint(result),
        }
        atomic_json(directory / "manifest.json", manifest)
        read_interpretation(directory, manifest_sha256=fingerprint(manifest))
        charged = (
            allowance
            if outcome["state"] == "uncertain"
            else Decimal(outcome["cost_usd"])
        )
        if outcome["cost_usd"] is not None:
            charged = max(charged, Decimal(outcome["cost_usd"]))
        entry.update(
            state=outcome["state"],
            charged_usd=str(charged),
            outcome=outcome,
            release=str(directory.relative_to(store.root)),
            manifest_sha256=fingerprint(manifest),
            completed_at=result["completed_at"],
            wall_seconds=result["wall_seconds"],
        )
        store.save(state)
        return {"origin": "provider", **entry}


def review_length_failure(key, *, store):
    """Record an inspected length failure as retained evidence, never as success."""
    # Same lock order as the batch scheduler: no review changes during a batch.
    with store.lock(name=".batch.lock"), store.lock():
        inspect_study(store)
        state = store.load()
        if key not in state["attempts"]:
            raise ValueError("No recorded attempt for this request")
        attempt = state["attempts"][key]
        if not attempt.get("release"):
            raise ValueError("Attempt has no verified receipt")
        receipt = read_interpretation(
            store.root / attempt["release"],
            manifest_sha256=attempt["manifest_sha256"],
        )
        require_reviewable(attempt, receipt)
        reviews = state.setdefault("failure_reviews", {})
        if key not in reviews:
            reviews[key] = {**review_identity(state, key), "reviewed_at": now()}
            store.save(state)
        return {
            "state": "failure_retained",
            "review": reviews[key],
            "attempt_state": attempt["state"],
            "replay_enabled": False,
            "reserve_enabled": state["protocol"].get("reserve_enabled", False),
            "model_requests": 0,
        }
