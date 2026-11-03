"""Full lifecycle uses real portable artifacts and synthetic model outputs."""

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from tests.unit.test_topic_corpus import fake_execute, pilot  # noqa: F401
from voc.embeddings.shared.contracts import fingerprint
from voc_ml.topics import corpus, full_corpus, production, supervision
from voc_ml.topics.cli import topics
from voc_ml.topics.resources import GIB, resource_preflight


def resources(root, **kwargs):
    return resource_preflight(
        root,
        **kwargs,
        observe=lambda: {
            "available_headroom_bytes": 8 * GIB,
            "cgroup_current_bytes": GIB,
            "effective_cpu_capacity": 4,
        },
        disk_usage=lambda _: SimpleNamespace(free=20 * GIB),
    )


def measured_execute(run_id, *, store):
    result = fake_execute(run_id, store=store)
    return result | {
        "supervision": {
            "samples": 10,
            "wall_seconds": 1,
            "peak_group_rss_bytes": GIB,
            "peak_cgroup_bytes": 2 * GIB,
        }
    }


@pytest.fixture
def completed(pilot):  # noqa: F811 - imported pytest fixture
    pilot.create()
    for variant in corpus.ORDER:
        corpus.run_pilot(
            variant,
            store=pilot.store,
            resources=pilot.resources,
            metadata=pilot.metadata,
            execute=measured_execute,
        )
    return pilot


def test_full_lifecycle_preserves_pilot_and_parent_and_blocks_replay(completed):
    store = completed.store
    pilot_path = store.root / "topics/corpus/pilot-v1/pilot.json"
    before = pilot_path.read_bytes()
    created = full_corpus.create_full(
        store=store, resources=resources, metadata=completed.metadata
    )
    assert full_corpus.create_full(store=store)["study_id"] == created["study_id"]
    assert created["protocol"]["input"]["selection"]["mode"] == "full"
    assert created["study_id"] != json.loads(before)["study_id"]
    for variant in corpus.ORDER:
        result = full_corpus.run_full(
            variant,
            store=store,
            resources=resources,
            metadata=completed.metadata,
            execute=measured_execute,
        )
    assert result["full_corpus_enabled"]
    assert len(result["summaries"]) == 3
    g3, r2 = [result["entries"][key] for key in ("G3", "R2")]
    child = store.load(r2["run_id"])
    assert child["parent"]["run_id"] == g3["run_id"]
    assert child["parent"]["run_id"] != json.loads(before)["entries"]["G3"]["run_id"]
    assert child["operation_budget"]["worker_seconds"] == 1800
    assert child["operation_budget"]["operation_seconds"] == 2400
    assert child["configuration"]["execution"]["worker_seconds"] == 3600
    assert pilot_path.read_bytes() == before
    with pytest.raises(ValueError, match="already recorded"):
        full_corpus.run_full("G0", store=store)


def test_full_requires_completed_pilot_without_creating_producers(pilot):  # noqa: F811
    pilot.create()
    with pytest.raises(ValueError, match="all three"):
        full_corpus.create_full(
            store=pilot.store, resources=resources, metadata=pilot.metadata
        )
    assert not list(pilot.store.root.glob("topics/runs/*"))


@pytest.mark.parametrize(
    "change", ["measurement", "environment", "input", "model", "resources"]
)
def test_full_creation_rejects_incompatible_or_unverified_evidence(completed, change):
    store = completed.store
    kwargs = dict(store=store, resources=resources, metadata=completed.metadata)
    if change in ("measurement", "model"):
        path = store.root / "topics/corpus/pilot-v1/pilot.json"
        data = json.loads(path.read_bytes())
        if change == "measurement":
            data["entries"]["G0"]["supervision"] = None
        else:
            data["protocol"]["configurations"]["G0"]["model"]["min_cluster_size"] = 20
            data["protocol_sha256"] = fingerprint(data["protocol"])
        path.write_text(json.dumps(data))
    elif change == "environment":
        kwargs["metadata"] = lambda: (
            completed.metadata() | {"software": {"numpy": "other"}}
        )
    elif change == "input":
        from voc_ml.topics.preflight import preflight

        def mismatched(*args):
            report = preflight(*args)
            report["input"]["population_selection_sha256"] = "f" * 64
            return report

        kwargs["prepare"] = mismatched
    else:
        kwargs["resources"] = lambda *a, **k: (
            resources(*a, **k) | {"resource_budget_fits": False, "reasons": ["margin"]}
        )
    before = set(store.root.glob("topics/runs/*"))
    with pytest.raises(ValueError):
        full_corpus.create_full(**kwargs)
    assert before == set(store.root.glob("topics/runs/*"))
    assert not (store.root / "topics/corpus/full-v1/full.json").exists()


def test_full_failure_cannot_retry_or_advance(completed):
    full_corpus.create_full(
        store=completed.store, resources=resources, metadata=completed.metadata
    )

    def fail(*args, **kwargs):
        raise TimeoutError("synthetic")

    with pytest.raises(TimeoutError):
        full_corpus.run_full(
            "G0",
            store=completed.store,
            resources=resources,
            metadata=completed.metadata,
            execute=fail,
        )
    with pytest.raises(ValueError, match="already recorded"):
        full_corpus.run_full("G0", store=completed.store)
    with pytest.raises(ValueError, match="preceding"):
        full_corpus.run_full("G3", store=completed.store)
    assert (
        full_corpus.inspect_full(store=completed.store)["entries"]["G0"]["state"]
        == "failed"
    )


def test_pilot_and_full_share_exclusive_operation_lock(completed):
    with corpus._locked(completed.store):
        with pytest.raises(ValueError, match="Another process"):
            full_corpus.create_full(store=completed.store)


def test_full_resource_margin_and_time_budget(tmp_path):
    def observed():
        return {
            "available_headroom_bytes": 6 * GIB - 1,
            "cgroup_current_bytes": 4 * GIB + 1,
            "effective_cpu_capacity": 4,
        }

    report = resource_preflight(
        tmp_path,
        scope="full",
        rows=84479,
        variant="R2",
        observe=observed,
        disk_usage=lambda _: SimpleNamespace(free=20 * GIB),
    )
    assert report["reasons"] == [
        "insufficient_shared_memory_margin",
        "insufficient_container_memory_margin",
    ]
    assert report["budget"]["worker_seconds"] == 1800
    assert report["budget"]["operation_seconds"] == 2400
    assert report["disk"]["planned_work_bytes"] > 3 * 84479 * 384 * 4


def test_full_cli_routes_operations_without_implicit_execution(completed, monkeypatch):
    import voc_ml.topics.store

    monkeypatch.setattr(voc_ml.topics.store, "local_store", lambda: completed.store)
    monkeypatch.setattr(full_corpus, "resource_preflight", resources)
    monkeypatch.setattr(full_corpus, "environment", completed.metadata)
    result = CliRunner().invoke(topics, ["corpus", "full", "create"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["entries"] == {}
    result = CliRunner().invoke(topics, ["corpus", "full", "resources"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["budget"]["scope"] == "full"


def test_supervisor_receives_frozen_limits_and_worker_receives_refinement_timeout(
    completed, monkeypatch
):
    store = completed.store
    full_corpus.create_full(
        store=store, resources=resources, metadata=completed.metadata
    )
    for variant in ("G0", "G3"):
        full_corpus.run_full(
            variant,
            store=store,
            resources=resources,
            metadata=completed.metadata,
            execute=measured_execute,
        )
    from tests.unit.test_topic_corpus import fake_worker

    seen = {}

    def worker(folder, config):
        seen["worker_seconds"] = config.timeout_seconds
        fake_worker(folder, config)

    def fake_supervise(command, log_path, **kwargs):
        seen.update(kwargs)
        production.execute_local(command[3], store=store, worker=worker)
        return {"wall_seconds": 1}

    monkeypatch.setattr(supervision, "supervise", fake_supervise)
    full_corpus.run_full(
        "R2",
        store=store,
        resources=resources,
        metadata=completed.metadata,
        execute=supervision.run_supervised,
    )
    assert seen["worker_seconds"] == 1800
    assert seen["seconds"] == 2400
    assert seen["rss_bytes"] == 4 * GIB
    assert seen["container_bytes"] == 8 * GIB


@pytest.mark.parametrize("pilot", [84479], indirect=True)
def test_real_full_population_selection_and_portable_export(completed):
    created = full_corpus.create_full(
        store=completed.store, resources=resources, metadata=completed.metadata
    )
    data = created["protocol"]["input"]
    assert data["selected_rows"] == data["population_rows"] == 84479
    assert data["selection_sha256"] == data["population_selection_sha256"]
    assert sum(item["selected"] for item in data["coverage"]) == 84479
    result = full_corpus.run_full(
        "G0",
        store=completed.store,
        resources=resources,
        metadata=completed.metadata,
        execute=measured_execute,
    )
    assert result["summaries"][0]["rows"] == 84479
    assert (
        result["entries"]["G0"]["run_id"]
        != created["protocol"]["pilot"]["results"]["G0"]["run_id"]
    )
