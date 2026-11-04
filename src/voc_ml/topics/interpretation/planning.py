"""Build an offline draft of cases and prompts with explicit read dependencies."""

from dataclasses import asdict

from voc.embeddings.shared.contracts import fingerprint
from voc.topics import read_topics

from .files import read_exposure
from .preparation import _prepare_cases, _read_pair, _summary
from .prompts import build_request
from .selection import propose_partitions

DEVELOPMENT_CONDITIONS = (("R0", "P0"), ("R2", "P0"), ("R2", "P1"), ("R2", "P2"))


def propose_study(config, *, reader=read_topics, exposure_reader=read_exposure):
    grouping, representation = _read_pair(config.inputs, reader)
    exposed, sources = exposure_reader(
        config.exposure_files, grouping.reviews.record_id
    )
    partitions = propose_partitions(
        grouping, config.known_topics, exposed, seed=config.seed
    )
    cases = {
        name: _prepare_cases(config.inputs, partitions[name], grouping, representation)[
            "cases"
        ]
        for name in ("development", "reserve")
    }
    requests = [
        build_request(case, rep, config.prompts[prompt], config.generation)
        for case in cases["development"]
        for rep, prompt in DEVELOPMENT_CONDITIONS
    ]
    plan = {
        "input": _summary(config.inputs, grouping, representation),
        "partitions": partitions,
        "exposure_sources": sources,
        "cases": cases,
        "cases_sha256": {name: fingerprint(items) for name, items in cases.items()},
        "prompts": {
            name: {"version": p.version, "text": p.text, "sha256": p.sha256}
            for name, p in config.prompts.items()
        },
        "generation": asdict(config.generation),
        "development_conditions": [list(x) for x in DEVELOPMENT_CONDITIONS],
        "development_requests": requests,
        "reserve_policy": "R2/P0 versus R2/selected-prompt after development selection; no reserve requests built yet",
        "proposal": {
            "max_provider_attempts": config.max_provider_attempts,
            "proposed_max_cost_usd": config.proposed_max_cost_usd,
            "max_reserve_requests": 24,
            "development_requests": len(requests),
        },
        "state": "draft",
        "execution_ready": False,
        "pending": [
            "development_case_review",
            "granularity_rubric",
            "representation_scope_confirmation",
            "provider_model_and_price_check",
            "provider_budget",
            "producer",
        ],
        "model_requests": 0,
        "run_created": False,
    }
    return {
        "schema_version": "voc-topic-interpretation-draft-v1",
        "plan_sha256": fingerprint(plan),
        "plan": plan,
    }


def summarize_draft(draft):
    """Expose no reserve text or words in CLI output or review material."""
    plan = draft["plan"]
    return {
        "state": plan["state"],
        "plan_sha256": draft["plan_sha256"],
        "input_sha256": plan["input"]["input_sha256"],
        "development_topics": [c["topic_id"] for c in plan["cases"]["development"]],
        "reserve_topics_count": len(plan["cases"]["reserve"]),
        "reserve_contents_hidden": True,
        "audit": plan["partitions"]["audit"],
        "prompts": {
            k: {"version": p["version"], "sha256": p["sha256"]}
            for k, p in plan["prompts"].items()
        },
        "proposal": plan["proposal"],
        "execution_ready": False,
        "pending": plan["pending"],
        "model_requests": 0,
        "run_created": False,
    }
