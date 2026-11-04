"""Portable interpretation receipts. No producer, network, model or tracking imports."""

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from voc.embeddings.shared.contracts import canonical_json, fingerprint, require_digest

from .interpretation import normalize_response, validate_case


def assess_response(response, case, provider, allowance):
    """Separate known billing from structural validity. Semantic review stays human."""
    outcome = {
        "state": "uncertain",
        "cost_usd": None,
        "description": None,
        "error": "missing_or_invalid_usage",
    }
    try:
        usage = response["usage"]
        cost = Decimal(str(usage["cost"]))
        if not cost.is_finite() or cost < 0 or isinstance(usage["cost"], bool):
            return outcome
        for field in ("prompt_tokens", "completion_tokens"):
            if type(usage[field]) is not int or usage[field] < 0:
                return outcome
        outcome["cost_usd"] = str(cost)
        if (
            cost > Decimal(allowance)
            or usage["prompt_tokens"] > provider["input_token_ceiling"]
            or usage["completion_tokens"] > provider["output_token_ceiling"]
        ):
            outcome["error"] = "billing_allowance_exceeded"
            return outcome
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return outcome
    outcome.update(state="invalid", error="invalid_response")
    try:
        if (
            response["model"] != provider["model"]
            or response["provider"] != provider["provider_name"]
        ):
            outcome["error"] = "provider_or_model_mismatch"
            return outcome
        if not isinstance(response["id"], str) or not response["id"]:
            return outcome
        choices = response["choices"]
        if len(choices) != 1 or choices[0]["finish_reason"] != "stop":
            outcome["error"] = "incomplete_response"
            return outcome
        message = choices[0]["message"]
        if message.get("refusal") or message.get("tool_calls"):
            return outcome
        result = json.loads(message["content"])
        description = normalize_response(result, case["evidence"])
        outcome.update(state="accepted", error=None, description=description)
    except (KeyError, TypeError, ValueError):
        pass
    return outcome


def read_interpretation(directory, *, manifest_sha256):
    """Read an immutable one-request receipt with caller-pinned manifest hash."""
    require_digest(manifest_sha256)
    root = Path(directory)

    def read(name):
        path = root / name
        if path.is_symlink() or not 0 < path.stat().st_size <= 4 * 2**20:
            raise ValueError("Invalid interpretation artifact")
        return path.read_bytes()

    raw = read("manifest.json")
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise ValueError("Interpretation manifest checksum differs")
    manifest = json.loads(raw)
    if manifest["schema_version"] != "voc-interpretation-release-v1":
        raise ValueError("Unsupported interpretation release")
    raw = read("result.json")
    if hashlib.sha256(raw).hexdigest() != manifest["result_sha256"]:
        raise ValueError("Interpretation result checksum differs")
    result = json.loads(raw)
    if result.get("schema_version") not in (
        "voc-interpretation-result-v1",
        "voc-interpretation-result-v2",
    ):
        raise ValueError("Unsupported interpretation result")
    if result["protocol_sha256"] != manifest["protocol_sha256"]:
        raise ValueError("Protocol identity differs")
    require_digest(result["protocol_sha256"])
    validate_case(result["case"])
    request = result["request"]
    if fingerprint(request["body"]) != request["body_sha256"]:
        raise ValueError("Request identity differs")
    if request["evidence_sha256"] != result["case"]["evidence_sha256"]:
        raise ValueError("Evidence identity differs")
    if request["topic_id"] != result["case"]["topic_id"]:
        raise ValueError("Topic identity differs")
    if (
        hashlib.sha256(request["body"]["messages"][0]["content"].encode()).hexdigest()
        != request["prompt_sha256"]
    ):
        raise ValueError("Prompt identity differs")
    if request["body"]["model"] != result["provider"]["model"]:
        raise ValueError("Requested model differs from provider policy")
    payload = json.loads(request["body"]["messages"][1]["content"])
    fixed = result["schema_version"] == "voc-interpretation-result-v2"
    if fixed:
        if request.get("mechanism") != "voc-dev-v3-sdk-v1":
            raise ValueError("Unknown fixed interpretation mechanism")
        if json.loads(request["body_json"]) != request["body"]:
            raise ValueError("Ordered SDK request differs")
        if (
            payload.get("dataset_release_id")
            != result["source_input"]["identity"]["dataset"]["artifact_id"]
        ):
            raise ValueError("Dataset identity differs")
        if result["provider"]["text_policy"] != "voc-dev-evidence-with-record-ids-v1":
            raise ValueError("Evidence policy differs")
    fields = (
        ("label", "record_id", "text", "truncated")
        if fixed
        else ("label", "text", "truncated")
    )
    expected = [{k: e[k] for k in fields} for e in result["case"]["evidence"]]
    if (
        payload["evidence"] != expected
        or payload["keywords"] != result["case"]["keywords"][request["representation"]]
    ):
        raise ValueError("Request evidence or words differ")
    expected_outcome = assess_response(
        result["response"], result["case"], result["provider"], result["allowance_usd"]
    )
    if result["outcome"] != expected_outcome:
        raise ValueError("Stored response validation differs")
    canonical_json(result)
    return result
