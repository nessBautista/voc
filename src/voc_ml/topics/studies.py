"""Small sequential G/R studies with frozen inputs and durable attempt budgets."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from voc.embeddings.shared.contracts import canonical_json, fingerprint, require_uuid
from voc.embeddings.shared.filesystem import sync_directory
from voc.topics import read_topics
from voc.topics.comparison import compare_assignments, review_sample, summarize

from .config import TopicConfig
from .models import representation_policy
from .production import create_variant, read_run_input
from .representation import RepresentationConfig
from .store import now
from .workflow import execute_workflow

VARIANTS = {
    "G0": {},
    "G1": {"n_components": 4},
    "G2": {"min_cluster_size": 20},
    "G3": {"cluster_selection_method": "leaf"},
}


def _directory(store, study_id):
    require_uuid(study_id, "study_id")
    return store.root / "topics/studies" / study_id


def _save(folder, state):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(state))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(folder / "study.json")
        sync_directory(folder)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_study(study_id, *, store):
    state = json.loads((_directory(store, study_id) / "study.json").read_bytes())
    if (
        state["study_id"] != study_id
        or state["schema_version"] != "voc-topic-study-v1"
        or fingerprint(state["protocol"]) != state["protocol_sha256"]
    ):
        raise ValueError("Study identity or protocol changed")
    return state


@contextmanager
def _locked(study_id, store):
    folder = _directory(store, study_id)
    with (folder / "study.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another process owns this study") from error
        try:
            yield folder, load_study(study_id, store=store)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read(entry, store):
    bundle = read_topics(
        store.directory(entry["run_id"]) / "release",
        manifest_sha256=entry["manifest_sha256"],
    )
    if bundle.info["run_id"] != entry["run_id"]:
        raise ValueError("Study entry identity differs from release")
    return bundle


def create_study(parent_run_id, manifest_sha256, *, store):
    reference = {"run_id": parent_run_id, "manifest_sha256": manifest_sha256}
    parent = _read(reference, store)
    config = TopicConfig()
    if (
        parent.info["schema_version"] != "voc-local-topics-v1"
        or parent.info["configuration"] != config.as_dict()
    ):
        raise ValueError("Study requires the unchanged G0 configuration")
    if parent.execution["fit"]["representation"] != representation_policy(config):
        raise ValueError("G0 lexical policy differs from current model factory")
    inputs = read_run_input(parent_run_id, store=store)
    if inputs.info != parent.info["input"] or not inputs.reviews.equals(parent.reviews):
        raise ValueError("G0 working input differs from release")
    protocol = {
        "parent": reference,
        "input": parent.info["input"],
        "base_configuration": config.as_dict(),
        "variants": VARIANTS,
        "software": parent.execution["fit"]["software"],
        "representation_policy": parent.execution["fit"]["representation"],
        "initial_seed": 42,
        "sensitivity_seeds": [13, 73],
        "max_new_fits": 7,
        "max_refinements": 2,
        "representations": {
            "R1": RepresentationConfig(method="keybert").as_dict(),
            "R2": RepresentationConfig(method="keybert_mmr").as_dict(),
        },
        "review_policy": "three largest + up to three seeded topics, three examples each, five outliers",
        "review_seed": 42,
        "automatic_retries": False,
    }
    study_id = str(uuid4())
    folder = _directory(store, study_id)
    folder.mkdir(parents=True, exist_ok=False)
    state = {
        "schema_version": "voc-topic-study-v1",
        "study_id": study_id,
        "created_at": now(),
        "protocol": protocol,
        "protocol_sha256": fingerprint(protocol),
        "entries": {
            "G0-42": {**reference, "state": "reused", "variant": "G0", "seed": 42}
        },
        "preselection": None,
        "representation_parent": None,
    }
    _save(folder, state)
    return state


def _reason(reviewer, rationale):
    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or not isinstance(rationale, str)
        or not rationale.strip()
    ):
        raise ValueError("Reviewer and rationale are required")
    return {
        "reviewer": reviewer.strip(),
        "rationale": rationale.strip(),
        "recorded_at": now(),
    }


def select_candidates(study_id, variants, reviewer, rationale, *, store):
    decision = _reason(reviewer, rationale)
    if (
        not 1 <= len(variants) <= 2
        or len(set(variants)) != len(variants)
        or not set(variants) <= set(VARIANTS)
    ):
        raise ValueError("Select one or two distinct G variants")
    with _locked(study_id, store) as (folder, state):
        if state["preselection"] is not None:
            raise ValueError("Sensitivity candidates are already frozen")
        if any(
            state["entries"].get(f"{name}-42", {}).get("state")
            not in ("completed", "failed")
            for name in ("G1", "G2", "G3")
        ):
            raise ValueError("Finish the initial attempts before preselection")
        for name in variants:
            entry = state["entries"][f"{name}-42"]
            if entry["state"] not in ("completed", "reused"):
                raise ValueError("A failed variant cannot be preselected")
            _read(entry, store)
        state["preselection"] = {**decision, "variants": list(variants)}
        _save(folder, state)
    return state


def select_representation_parent(
    study_id, variant, reviewer, rationale, *, store, encoder_metadata=None
):
    decision = _reason(reviewer, rationale)
    with _locked(study_id, store) as (folder, state):
        if state["representation_parent"] is not None:
            raise ValueError("Representation parent is already frozen")
        if (
            state["preselection"] is None
            or variant not in state["preselection"]["variants"]
        ):
            raise ValueError("Choose a preselected variant after sensitivity attempts")
        for name in state["preselection"]["variants"]:
            for seed in (13, 73):
                if state["entries"].get(f"{name}-{seed}", {}).get("state") not in (
                    "completed",
                    "failed",
                ):
                    raise ValueError("Sensitivity attempts are still pending")
        key = f"{variant}-42"
        _read(state["entries"][key], store)
        if encoder_metadata is None:
            from voc.embeddings.shared.profiles import resolve_profile
            from voc_ml.embeddings.encoding.sentence_encoder import encoder_descriptor

            encoder_metadata = lambda: encoder_descriptor(resolve_profile())
        state["representation_parent"] = {
            **decision,
            "entry": key,
            "encoder_descriptor": encoder_metadata(),
        }
        # R0 is an explicitly reused result, never another grouping fit.
        state["entries"]["R0"] = {
            **state["entries"][key],
            "state": "reused",
            "variant": "R0",
            "parent_entry": key,
        }
        _save(folder, state)
    return state


def run_variant(study_id, variant, *, seed=42, store, execute=None):
    execute = execute if execute is not None else execute_workflow
    with _locked(study_id, store) as (folder, state):
        protocol = state["protocol"]
        representation = None
        if variant in ("R1", "R2"):
            if seed != 42 or state["representation_parent"] is None:
                raise ValueError("Freeze a seed-42 representation parent first")
            key = variant
            parent_entry = state["representation_parent"]["entry"]
            representation = RepresentationConfig(
                **protocol["representations"][variant]
            )
            parent = state["entries"][parent_entry]
            config = TopicConfig(**_read(parent, store).info["configuration"])
        elif variant in VARIANTS:
            if (
                type(seed) is not int
                or seed not in (42, 13, 73)
                or (variant == "G0" and seed == 42)
            ):
                raise ValueError("Invalid G variant/seed")
            if seed != 42 and (
                state["preselection"] is None
                or variant not in state["preselection"]["variants"]
            ):
                raise ValueError("Freeze sensitivity candidates before running seeds")
            key, parent_entry = f"{variant}-{seed}", "G0-42"
            parent = state["entries"][parent_entry]
            config = replace(
                TopicConfig(**protocol["base_configuration"]),
                **protocol["variants"][variant],
                random_state=seed,
            )
        else:
            raise ValueError("Unknown study variant")
        if key in state["entries"]:
            raise ValueError(
                "Attempt already recorded. No automatic replay, including failures"
            )
        baseline = _read(protocol["parent"], store)
        if baseline.info["input"] != protocol["input"]:
            raise ValueError("Frozen input differs from G0")
        # Reserve the attempt before creating a producer or invoking any external service.
        entry = {
            "variant": variant,
            "seed": seed,
            "state": "running",
            "started_at": now(),
            "parent_entry": parent_entry,
            "run_id": None,
        }
        state["entries"][key] = entry
        _save(folder, state)
        try:
            run = create_variant(
                parent["run_id"],
                parent["manifest_sha256"],
                config,
                store=store,
                representation_config=representation,
            )
            if representation is not None:
                store.update(
                    run["run_id"],
                    expected_encoder=state["representation_parent"][
                        "encoder_descriptor"
                    ],
                )
            entry["run_id"] = run["run_id"]
            _save(folder, state)
            result = execute(run["run_id"], store=store)
            if result["state"] != "completed":
                raise ValueError("Workflow did not complete")
            entry["manifest_sha256"] = result["result"]["manifest_sha256"]
            bundle = _read(entry, store)
            if bundle.info["input"] != protocol["input"]:
                raise ValueError("Completed variant input differs from protocol")
            entry.update(state="completed", completed_at=now())
        except BaseException as error:
            entry.update(
                state="failed",
                error={"type": type(error).__name__, "message": str(error)[:2000]},
                completed_at=now(),
            )
            _save(folder, state)
            raise
        _save(folder, state)
    return state


def inspect_study(study_id, *, store):
    """Verify completed references and report pending reviews. Does not run models."""
    state = load_study(study_id, store=store)
    summaries, comparisons, reviews, bundles = [], [], {}, {}
    baseline = _read(state["protocol"]["parent"], store)
    for key, entry in state["entries"].items():
        if entry["state"] not in ("completed", "reused"):
            summaries.append({"entry": key, **entry})
            continue
        bundle = _read(entry, store)
        comparison = compare_assignments(baseline, bundle)
        summaries.append({"entry": key, **entry, **summarize(bundle)})
        if key.startswith("G"):
            comparisons.append({"left": "G0-42", "right": key, **comparison})
        bundles[key] = bundle
        reviews[key] = review_sample(bundle)
    # Seed pairs within a configuration are more informative than G0-only ARI.
    for name in VARIANTS:
        for a, b in ((42, 13), (42, 73), (13, 73)):
            left, right = f"{name}-{a}", f"{name}-{b}"
            if left in bundles and right in bundles and left != "G0-42":
                comparisons.append(
                    {
                        "left": left,
                        "right": right,
                        **compare_assignments(bundles[left], bundles[right]),
                    }
                )
    return {
        **state,
        "summaries": summaries,
        "comparisons": comparisons,
        "review_templates": reviews,
    }
