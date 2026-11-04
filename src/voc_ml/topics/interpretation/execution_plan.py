"""Freeze a development protocol from the reviewed offline proposal."""

from copy import deepcopy
from decimal import Decimal

from voc.embeddings.shared.contracts import canonical_json, fingerprint

from .prompts import Generation, Prompt, build_request


def request_body(case, representation, prompt, generation, provider):
    prepared = build_request(case, representation, prompt, generation)
    body = prepared["request"]["body"]
    body.update(
        provider={
            "only": [provider["provider"]],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "max_price": {
                "prompt": float(provider["prompt_usd_per_million"]),
                "completion": float(provider["completion_usd_per_million"]),
                "request": 0,
            },
        },
        reasoning={"enabled": False},
        stream=False,
    )
    # Deliberately generous planning bound, not an exact provider tokenizer.
    estimate = 2 * len(canonical_json(body)) + 4096
    if estimate > provider["input_token_ceiling"]:
        raise ValueError("Request exceeds conservative input planning allowance")
    return {
        "body": body,
        "body_sha256": fingerprint(body),
        "input_planning_tokens": estimate,
        "evidence_sha256": case["evidence_sha256"],
        "prompt_version": prompt.version,
        "prompt_sha256": prompt.sha256,
        "topic_id": case["topic_id"],
        "representation": representation,
        "prompt": prompt.variant,
    }


def reserve_usd(provider):
    return (
        Decimal(provider["prompt_usd_per_million"]) * provider["input_token_ceiling"]
        + Decimal(provider["completion_usd_per_million"])
        * provider["output_token_ceiling"]
    ) / 1000000


def freeze_protocol(draft, provider):
    plan = draft["plan"]
    if fingerprint(plan) != draft["plan_sha256"] or plan["state"] != "draft":
        raise ValueError("Draft identity differs")
    if plan["generation"] != {
        "model": provider["model"],
        "temperature": 0.0,
        "max_tokens": 800,
    }:
        raise ValueError("Generation differs from the reviewed provider policy")
    if len(plan["cases"]["development"]) != 12 or len(plan["cases"]["reserve"]) != 12:
        raise ValueError("Expected twelve development and twelve reserved cases")
    prompts = {
        key: Prompt(key, value["version"], value["text"])
        for key, value in plan["prompts"].items()
    }
    if any(p.sha256 != plan["prompts"][key]["sha256"] for key, p in prompts.items()):
        raise ValueError("Prompt identity differs")
    generation = Generation(**plan["generation"])
    conditions = [("R0", "P0"), ("R2", "P0"), ("R2", "P1"), ("R2", "P2")]
    requests = {}
    for case in plan["cases"]["development"]:
        for rep, prompt in conditions:
            request = request_body(case, rep, prompts[prompt], generation, provider)
            requests[f"{case['topic_id']}-{rep}-{prompt}"] = request
    if len(requests) != 48:
        raise ValueError("Development request identities are not unique")
    bound = reserve_usd(provider) * provider["max_attempts"]
    if bound > Decimal(provider["max_cost_usd"]):
        raise ValueError("Maximum study allowance exceeds budget")
    return {
        "schema_version": "voc-interpretation-protocol-v1",
        "draft_sha256": draft["plan_sha256"],
        "input": deepcopy(plan["input"]),
        "cases": deepcopy(plan["cases"]["development"]),
        "reserve_cases_sha256": plan["cases_sha256"]["reserve"],
        "provider": deepcopy(provider),
        "requests": requests,
        "max_study_allowance_usd": str(bound),
        "per_attempt_allowance_usd": str(reserve_usd(provider)),
        "initial_requests": ["59-R2-P0", "1440-R2-P0"],
        "reserve_enabled": False,
        "review_policy": "development-review-2026-11-03-action-granularity-v1",
    }
