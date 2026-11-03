"""Freeze a full-population study from a completed, verified corpus pilot."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from voc.embeddings.shared.contracts import fingerprint
from voc.topics.scope import TopicSelection

from .corpus import (
    ORDER,
    _folder,
    _load,
    _locked,
    _save,
    _state_path,
    environment,
    inspect_pilot,
    run_pilot,
)
from .corpus_config import CorpusConfig
from .models import representation_policy
from .preflight import preflight
from .representation import RepresentationConfig
from .resources import resource_preflight
from .store import now


def full_resources(*, store, resources=None):
    """Read the pilot population and observe the proposed full grouping budget."""
    resources = resources if resources is not None else resource_preflight
    pilot = _load(_folder(store, "pilot"))
    return resources(
        store.root, scope="full", rows=pilot["protocol"]["input"]["population_rows"]
    )


def create_full(*, store, prepare=None, resources=None, metadata=None):
    """Explicitly freeze two full fits and one refinement. Does not start models."""
    prepare = prepare if prepare is not None else preflight
    resources = resources if resources is not None else resource_preflight
    metadata = metadata if metadata is not None else environment
    with _locked(store, "full") as folder:
        if _state_path(folder).exists():
            return _load(folder)
        pilot = inspect_pilot(store=store)
        if set(pilot["entries"]) != set(ORDER) or any(
            entry["state"] != "completed" for entry in pilot["entries"].values()
        ):
            raise ValueError("Complete and verify all three pilot operations first")
        prior = pilot["protocol"]
        for entry in pilot["entries"].values():
            measured = entry.get("supervision") or {}
            if (
                measured.get("samples", 0) < 1
                or not 0
                < measured.get("wall_seconds", 0)
                <= prior["budget"]["operation_seconds"]
                or not 0
                < measured.get("peak_group_rss_bytes", 0)
                <= prior["budget"]["process_tree_rss_bytes"]
                or not 0
                < measured.get("peak_cgroup_bytes", 0)
                <= prior["budget"]["container_bytes"]
            ):
                raise ValueError(
                    "Pilot supervision evidence is missing or exceeds budget"
                )
        current = metadata()
        for key in ("software", "encoder"):
            if current[key] != prior["environment"][key]:
                raise ValueError(
                    "Pilot software or encoder changed. Review before scaling"
                )
        rows = prior["input"]["population_rows"]
        observation = resources(store.root, scope="full", rows=rows)
        if not observation["resource_budget_fits"]:
            raise ValueError(f"Full resources unavailable: {observation['reasons']}")
        selection = TopicSelection("full", None, None)
        request = prior["requested"]
        checked = prepare(
            request["dataset_version"],
            pilot["embedding_directory"],
            request["embedding_manifest_sha256"],
            selection,
        )
        actual = checked["input"]
        if (
            checked["state"] != "inputs_verified"
            or actual["selection"] != selection.as_dict()
            or actual["selected_rows"] != rows
            or actual["selection_sha256"] != actual["population_selection_sha256"]
            or any(
                actual[key] != prior["input"][key]
                for key in (
                    "dataset",
                    "embedding_release_id",
                    "embedding_manifest_sha256",
                    "profile",
                    "population_rows",
                    "population_selection_sha256",
                )
            )
        ):
            raise ValueError("Full inputs differ from the verified pilot population")
        config = CorpusConfig(selection=selection, rows=rows, timeout_seconds=3600)
        configurations = {
            "G0": config.as_dict(),
            "G3": replace(
                config, model=config.model | {"cluster_selection_method": "leaf"}
            ).as_dict(),
        }
        representation = RepresentationConfig(method="keybert_mmr").as_dict()
        lexical = representation_policy(config)
        if (
            any(
                configurations[key]["model"] != prior["configurations"][key]["model"]
                for key in ("G0", "G3")
            )
            or representation != prior["representation"]
            or lexical != prior["lexical_policy"]
        ):
            raise ValueError(
                "Full study must preserve the pilot model and representation"
            )
        budgets = {key: dict(observation["budget"]) for key in ORDER}
        budgets["R2"].update(worker_seconds=1800, operation_seconds=2400)
        protocol = {
            "requested": request,
            "input": actual,
            "configurations": configurations,
            "representation": representation,
            "lexical_policy": lexical,
            "environment": current,
            "budgets": budgets,
            "disk_policy": {
                k: v for k, v in observation["disk"].items() if k != "free_bytes"
            },
            "pilot": {
                "study_id": pilot["study_id"],
                "protocol_sha256": pilot["protocol_sha256"],
                "results": {
                    key: {
                        name: pilot["entries"][key][name]
                        for name in ("run_id", "manifest_sha256", "supervision")
                    }
                    for key in ORDER
                },
            },
            "order": list(ORDER),
            "max_new_fits": 2,
            "max_refinements": 1,
            "automatic_retries": False,
            "full_corpus_enabled": True,
        }
        state = {
            "schema_version": "voc-topic-corpus-full-v1",
            "study_id": str(uuid4()),
            "created_at": now(),
            "protocol": protocol,
            "protocol_sha256": fingerprint(protocol),
            "embedding_directory": str(Path(pilot["embedding_directory"]).resolve()),
            "initial_resources": observation,
            "entries": {},
        }
        _save(folder, state)
        return state


def run_full(variant, *, store, **collaborators):
    return run_pilot(variant, store=store, scope="full", **collaborators)


def inspect_full(*, store):
    return inspect_pilot(store=store, scope="full")
