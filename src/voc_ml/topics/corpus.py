"""Frozen three-operation corpus studies with explicit scope and no retries."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from voc.embeddings.shared.contracts import canonical_json, fingerprint
from voc.embeddings.shared.filesystem import sync_directory
from voc.embeddings.shared.profiles import resolve_profile
from voc.topics import read_topics
from voc.topics.comparison import compare_assignments, summarize

from .corpus_config import CorpusConfig, resolve_config
from .models import representation_policy
from .preflight import preflight
from .production import create_run
from .provenance import code_provenance
from .representation import RepresentationConfig
from .resources import resource_preflight
from .store import now
from .supervision import run_supervised

ORDER = ("G0", "G3", "R2")


def environment():
    from voc_ml.embeddings.encoding.sentence_encoder import encoder_descriptor

    return {
        "software": {
            name: version(name)
            for name in (
                "bertopic",
                "umap-learn",
                "hdbscan",
                "scikit-learn",
                "numpy",
                "scipy",
                "numba",
            )
        },
        "encoder": encoder_descriptor(resolve_profile()),
        "code_identity": code_provenance()["code_identity"],
    }


def _folder(store, scope):
    if scope not in ("pilot", "full"):
        raise ValueError("Invalid corpus scope")
    return store.root / f"topics/corpus/{scope}-v1"


@contextmanager
def _locked(store, scope="pilot"):
    # Both scopes share operation ownership and preserve their separate records.
    folder = _folder(store, scope)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder.parent / "corpus.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another process owns a corpus operation") from error
        try:
            yield folder
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _state_path(folder):
    return folder / ("full.json" if folder.name == "full-v1" else "pilot.json")


def _save(folder, state):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(state))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(_state_path(folder))
        sync_directory(folder)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load(folder):
    state = json.loads(_state_path(folder).read_bytes())
    if (
        state["schema_version"]
        != (
            "voc-topic-corpus-full-v1"
            if folder.name == "full-v1"
            else "voc-topic-corpus-pilot-v1"
        )
        or fingerprint(state["protocol"]) != state["protocol_sha256"]
    ):
        raise ValueError("Corpus protocol identity changed")
    return state


def _read(entry, store):
    bundle = read_topics(
        store.directory(entry["run_id"]) / "release",
        manifest_sha256=entry["manifest_sha256"],
    )
    if bundle.info["run_id"] != entry["run_id"]:
        raise ValueError("Corpus result identity differs")
    return bundle


def create_pilot(
    dataset_version,
    directory,
    manifest_sha256,
    *,
    store,
    prepare=None,
    resources=None,
    metadata=None,
):
    prepare = prepare if prepare is not None else preflight
    resources = resources if resources is not None else resource_preflight
    metadata = metadata if metadata is not None else environment
    requested = {
        "dataset_version": dataset_version,
        "embedding_manifest_sha256": manifest_sha256,
    }
    with _locked(store) as folder:
        if (folder / "pilot.json").exists():
            saved = _load(folder)
            if saved["protocol"]["requested"] != requested:
                raise ValueError("Existing pilot uses different inputs")
            return saved
        observation = resources(store.root)
        if not observation["resource_budget_fits"]:
            raise ValueError(f"Pilot resources unavailable: {observation['reasons']}")
        checked = prepare(dataset_version, directory, manifest_sha256)
        config = CorpusConfig()
        if (
            checked["state"] != "inputs_verified"
            or checked["input"]["selection"] != config.selection.as_dict()
        ):
            raise ValueError("Pilot inputs were not verified")
        protocol = {
            "requested": requested,
            "input": checked["input"],
            "configurations": {
                "G0": config.as_dict(),
                "G3": replace(
                    config, model=config.model | {"cluster_selection_method": "leaf"}
                ).as_dict(),
            },
            "representation": RepresentationConfig(method="keybert_mmr").as_dict(),
            "lexical_policy": representation_policy(config),
            "environment": metadata(),
            "budget": observation["budget"],
            "disk_policy": {
                k: v for k, v in observation["disk"].items() if k != "free_bytes"
            },
            "order": list(ORDER),
            "max_new_fits": 2,
            "max_refinements": 1,
            "automatic_retries": False,
            "full_corpus_enabled": False,
        }
        state = {
            "schema_version": "voc-topic-corpus-pilot-v1",
            "study_id": str(uuid4()),
            "created_at": now(),
            "protocol": protocol,
            "protocol_sha256": fingerprint(protocol),
            "embedding_directory": str(Path(directory).resolve()),
            "initial_resources": observation,
            "entries": {},
        }
        _save(folder, state)
        return state


def run_pilot(
    variant, *, store, resources=None, metadata=None, execute=None, scope="pilot"
):
    """Execute one attempt in the explicitly selected frozen study."""
    if variant not in ORDER:
        raise ValueError("Corpus study supports G0, G3 and R2 only")
    resources = resources if resources is not None else resource_preflight
    metadata = metadata if metadata is not None else environment
    execute = execute if execute is not None else run_supervised
    with _locked(store, scope) as folder:
        state = _load(folder)
        protocol = state["protocol"]
        if variant in state["entries"]:
            raise ValueError("Attempt already recorded. No replay, including failures")
        if any(
            state["entries"].get(key, {}).get("state") != "completed"
            for key in ORDER[: ORDER.index(variant)]
        ):
            raise ValueError("Complete preceding corpus operations first")
        if metadata() != protocol["environment"]:
            raise ValueError("Corpus environment changed since protocol creation")
        observation = (
            resources(store.root)
            if scope == "pilot"
            else resources(
                store.root,
                scope="full",
                rows=protocol["input"]["population_rows"],
                variant=variant,
            )
        )
        budget = (
            protocol["budget"] if scope == "pilot" else protocol["budgets"][variant]
        )
        if observation["budget"] != budget or not observation["resource_budget_fits"]:
            raise ValueError(
                f"Corpus resource budget unavailable: {observation['reasons']}"
            )
        config = resolve_config(
            protocol["configurations"]["G3" if variant == "R2" else variant]
        )
        if representation_policy(config) != protocol["lexical_policy"]:
            raise ValueError("Corpus lexical policy changed")
        # Reserve the attempt before creating the producer or starting any workflow.
        entry = {
            "state": "running",
            "started_at": now(),
            "run_id": None,
            "resource_preflight": observation,
        }
        state["entries"][variant] = entry
        _save(folder, state)
        try:
            run = create_run(
                protocol["requested"]["dataset_version"],
                state["embedding_directory"],
                protocol["requested"]["embedding_manifest_sha256"],
                config,
                store=store,
            )
            if variant == "R2":
                parent = state["entries"]["G3"]
                # Parent files are verified inside supervised pinning, not in the launcher.
                store.update(
                    run["run_id"],
                    operation="representation",
                    parent={
                        "run_id": parent["run_id"],
                        "manifest_sha256": parent["manifest_sha256"],
                    },
                    representation_config=protocol["representation"],
                )
            entry["run_id"] = run["run_id"]
            store.update(
                run["run_id"],
                expected_input=protocol["input"],
                expected_software=protocol["environment"]["software"],
                expected_encoder=protocol["environment"]["encoder"],
                corpus_protocol_sha256=state["protocol_sha256"],
                operation_budget=budget,
            )
            _save(folder, state)
            result = execute(run["run_id"], store=store)
            if result["state"] != "completed":
                raise ValueError("Corpus workflow did not complete")
            entry["manifest_sha256"] = result["result"]["manifest_sha256"]
            bundle = _read(entry, store)
            if bundle.info["input"] != protocol["input"]:
                raise ValueError("Corpus output differs from frozen input")
            entry.update(
                state="completed",
                completed_at=now(),
                supervision=result.get("supervision"),
                counts=bundle.info["counts"],
            )
        except BaseException as error:
            entry.update(
                state="failed",
                completed_at=now(),
                error={"type": type(error).__name__, "message": str(error)[:2000]},
            )
            _save(folder, state)
            raise
        _save(folder, state)
    return inspect_pilot(store=store, scope=scope)


def inspect_pilot(*, store, scope="pilot"):
    folder = _folder(store, scope)
    state = _load(folder)
    summaries, bundles = [], {}
    for key, entry in state["entries"].items():
        if entry["state"] == "completed":
            bundle = _read(entry, store)
            if bundle.info["input"] != state["protocol"]["input"]:
                raise ValueError("Corpus result input differs from protocol")
            bundles[key] = bundle
            summaries.append(
                {
                    "variant": key,
                    **summarize(bundle),
                    "supervision": entry.get("supervision"),
                }
            )
    comparisons = []
    if "G0" in bundles and "G3" in bundles:
        comparisons.append(
            {
                "left": "G0",
                "right": "G3",
                **compare_assignments(bundles["G0"], bundles["G3"]),
            }
        )
    return {
        **state,
        "summaries": summaries,
        "comparisons": comparisons,
        "full_corpus_enabled": scope == "full",
        "model_requests": 0,
    }
