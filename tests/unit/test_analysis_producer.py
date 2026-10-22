"""Completion boundaries, checked step exchange and machine-readable commands."""

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from click.testing import CliRunner

from tests.unit.test_analysis_inputs import fixture_inputs
from voc.analysis import Keyword, ReviewAssignment, ReviewSentiment, TopicRecord, store
from voc.analysis.queries import query
from voc.embeddings.contracts import text_hash
from voc_ml.analysis import AnalysisConfig, artifacts, select_inputs
from voc_ml.analysis import producer as p
from voc_ml.analysis import workflow as w
from voc_ml.cli import main
from voc_ml.sentiment import SentimentResult
from voc_ml.topics.bertopic import TopicDiscovery


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def operations(runtime, monkeypatch):
    inputs = select_inputs(*fixture_inputs(48))
    monkeypatch.setattr(w, "pin_inputs", Mock(return_value=inputs))
    assignments = tuple(
        ReviewAssignment(rid, 0, 1.0) for rid in inputs.reviews.record_id
    )
    discovered = TopicDiscovery(
        assignments,
        (TopicRecord(0, (Keyword("reseña", 1.0),), (assignments[0].record_id,)),),
        {
            "input_id": inputs.info["input_id"],
            "configuration": AnalysisConfig().as_dict(),
            "software": {"bertopic": "test"},
            "reduction_reused": True,
            "topic_count": 1,
            "unassigned_reviews": 0,
        },
    )

    def discover(inputs, config):
        return TopicDiscovery(
            discovered.assignments,
            discovered.topics,
            discovered.report | {"configuration": config.as_dict()},
        )

    monkeypatch.setattr("voc_ml.topics.discover", discover)

    def classify(reviews, config, **kwargs):
        rows = tuple(
            ReviewSentiment(
                rid,
                "classified",
                "positive",
                {"negative": 0.1, "neutral": 0.2, "positive": 0.7},
                input_sha256=text_hash(text),
            )
            for rid, text in zip(reviews.record_id, reviews.model_text)
        )
        return SentimentResult(
            rows,
            (),
            {
                "state": "completed",
                "configuration": asdict(config),
                "progress_path": str(runtime / "progress.json"),
                "descriptor": {"software": {"torch": "test"}},
                "counts": {
                    "rows": len(rows),
                    "classified": len(rows),
                    "excluded": 0,
                    "failed": 0,
                    "computed_unique": 0,
                    "reused_unique": len(set(reviews.model_text)),
                    "truncated_rows": 0,
                },
            },
        )

    monkeypatch.setattr(w, "classify_reviews", classify)
    monkeypatch.setattr(
        "voc.collector.get_dataset", Mock(return_value=fixture_inputs(48)[0])
    )
    return inputs


def perform_steps(state):
    """Service-free orchestration, exercising actual local IO and result validation."""
    rid = state["run_id"]
    p.update_run(
        rid,
        zenml_run_id=str(uuid4()),
        mlflow_run_id="test-mlflow",
        project_identity="a" * 64,
        experiment_id="1",
    )
    inp = w.pin(rid)
    topic = w.topics(inp, rid) if state["kind"] == "analysis" else None
    sentiment = w.sentiment(inp, rid)
    w.evidence(inp, sentiment, rid, topic)
    current = p.load_run(rid)
    client = tracking_client(current)
    p.verify_tracking(client, current)
    return p.update_run(rid, tracking_verified=True)


def tracking_client(state):
    client = Mock()
    client.get_run.return_value = SimpleNamespace(
        info=SimpleNamespace(status="FINISHED", experiment_id="1"),
        data=SimpleNamespace(
            tags={
                "producer_run_id": state["run_id"],
                "zenml_run_id": state["zenml_run_id"],
            }
        ),
    )
    client.download_artifacts.return_value = state["evidence_reference"]["path"]
    return client


def test_complete_candidate_queryable_and_new_run_has_new_identity(
    operations, monkeypatch
):
    monkeypatch.setattr(p, "_launch", perform_steps)
    results = [p.execute_run(p.create_run()["run_id"]) for _ in range(2)]
    assert all(result["state"] == "completed" for result in results)
    assert results[0]["analysis_id"] != results[1]["analysis_id"]
    assert results[0]["run_id"] != results[0]["analysis_id"]
    assert results[0]["inputs"]["input_id"] == results[1]["inputs"]["input_id"]
    assert len(store.list_analyses()) == 2
    for result in results:
        assert p.inspect_run(result["run_id"])["counts"]["classified"] == 48
        assert query(result["analysis_id"])["state"] == "ok"
        assert (
            store.read_analysis(result["analysis_id"]).manifest.inputs
            == operations.info
        )
    with pytest.raises(ValueError, match="already launched"):
        p.execute_run(results[0]["run_id"])


def test_storage_failure_keeps_tracking_and_never_exposes_candidate(
    operations, monkeypatch
):
    monkeypatch.setattr(p, "_launch", perform_steps)
    original = store._write_database
    monkeypatch.setattr(
        store, "_write_database", Mock(side_effect=OSError("disk full"))
    )
    failed = p.execute_run(p.create_run()["run_id"])
    assert failed["state"] == "failed" and failed["analysis_id"] is None
    assert failed["tracking_verified"] and failed["mlflow_run_id"]
    assert p.inspect_run(failed["run_id"])["error"]["message"] == "disk full"
    assert store.list_analyses() == []
    monkeypatch.setattr(store, "_write_database", original)
    assert p.execute_run(p.create_run()["run_id"])["state"] == "completed"


def test_standalone_sentiment_needs_no_embedding_or_topic(operations, monkeypatch):
    monkeypatch.setattr(
        w, "pin_inputs", Mock(side_effect=AssertionError("No embeddings"))
    )
    monkeypatch.setattr(w, "topics", Mock(side_effect=AssertionError("No topics")))
    monkeypatch.setattr(p, "_launch", perform_steps)
    result = p.execute_run(p.create_run(kind="sentiment", limit=4)["run_id"])
    assert result["state"] == "completed" and result["analysis_id"] is None
    assert result["counts"]["classified"] == 4
    assert p.inspect_run(result["run_id"]) == result
    assert store.list_analyses() == []


@pytest.mark.parametrize(
    "failure", [RuntimeError("tracker unavailable"), KeyboardInterrupt(), SystemExit(2)]
)
def test_launch_failure_is_durable(runtime, monkeypatch, failure):
    monkeypatch.setattr(p, "_launch", Mock(side_effect=failure))
    state = p.execute_run(p.create_run()["run_id"])
    assert state["state"] == "failed"
    assert p.inspect_run(state["run_id"])["error"]["type"] == type(failure).__name__
    assert store.list_analyses() == []


@pytest.mark.parametrize("change", ["status", "experiment", "run", "zenml", "artifact"])
def test_tracking_verification_rejects_incomplete_or_different_evidence(
    operations, change
):
    state = p.create_run()
    state = p.update_run(state["run_id"], started_at=p.now())
    state = perform_steps(state)
    client = tracking_client(state)
    if change == "status":
        client.get_run.return_value.info.status = "RUNNING"
    elif change == "experiment":
        client.get_run.return_value.info.experiment_id = "wrong"
    elif change in ("run", "zenml"):
        key = "producer_run_id" if change == "run" else "zenml_run_id"
        client.get_run.return_value.data.tags[key] = "wrong"
    else:
        wrong = p.run_directory(state["run_id"]) / "wrong.json"
        wrong.write_text("{}")
        client.download_artifacts.return_value = str(wrong)
    with pytest.raises(ValueError):
        p.verify_tracking(client, state)


def test_checksums_prevent_mutated_inputs_and_results(operations):
    state = p.create_run()
    reference = w.pin(state["run_id"])
    record = artifacts.read_json(reference)
    Path(record["files"]["vectors.npy"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        artifacts.read_input(reference)
    with pytest.raises(ValueError, match="Tracking"):
        p.finalize(state)


def test_limit_overrides_selection_and_invalid_config_leaves_no_run(runtime):
    state = p.create_run(limit=40)
    assert state["configuration"]["analysis"]["selection"] == {
        "kind": "first-n",
        "size": 40,
        "seed": None,
    }
    assert state["requested"]["local_files_only"] is True
    before = list((runtime / "analysis-runs").iterdir())
    for kwargs in (
        {"limit": 2},
        {"limit": True},
        {"dataset_version": "../bad"},
        {"source": "local"},
    ):
        with pytest.raises(ValueError):
            p.create_run(**kwargs)
    assert list((runtime / "analysis-runs").iterdir()) == before


def test_cli_json_errors_and_inspection(runtime, operations, monkeypatch):
    runner = CliRunner()
    for args in (
        ["analysis", "run", "--limit", "bad", "--json"],
        ["analysis", "run", "--limit", "30", "--limit", "40", "--json"],
        ["analysis", "inspect", "bad", "--json"],
    ):
        result = runner.invoke(main, args)
        assert result.exit_code != 0
        assert json.loads(result.stdout)["state"] == "error"
    monkeypatch.setattr(p, "_launch", perform_steps)
    root = Path(__file__).resolve().parents[2]
    result = runner.invoke(
        main,
        [
            "analysis",
            "run",
            "--config",
            str(root / "config/analysis/bertopic.toml"),
            "--sentiment-config",
            str(root / "config/analysis/sentiment.toml"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    state = json.loads(result.stdout)
    assert state["state"] == "completed"
    inspected = runner.invoke(main, ["analysis", "inspect", state["run_id"], "--json"])
    assert inspected.exit_code == 0 and json.loads(inspected.stdout) == state
    wrong = runner.invoke(main, ["sentiment", "inspect", state["run_id"], "--json"])
    assert wrong.exit_code != 0 and json.loads(wrong.stdout)["state"] == "error"


def test_failed_cli_returns_json_and_nonzero(runtime, monkeypatch):
    monkeypatch.setattr(p, "_launch", Mock(side_effect=RuntimeError("service offline")))
    root = Path(__file__).resolve().parents[2]
    result = CliRunner().invoke(
        main,
        [
            "sentiment",
            "workflow",
            "--limit",
            "4",
            "--sentiment-config",
            str(root / "config/analysis/sentiment.toml"),
            "--json",
        ],
    )
    assert result.exit_code == 1
    state = json.loads(result.stdout)
    assert state["error"]["message"] == "service offline"
    assert p.load_run(state["run_id"])["state"] == "failed"


def test_experiment_names_preserve_storage_destination():
    from voc_ml.tracking import analysis_experiment_name, ensure_analysis_experiment

    storage = SimpleNamespace(
        artifact_destination="s3",
        installation_id="member-installation",
        mlflow_artifact_uri="s3://bucket/personal/mlflow",
    )
    client = Mock()
    client.get_experiment_by_name.return_value = None
    ensure_analysis_experiment(client, storage)
    client.create_experiment.assert_called_once_with(
        "voc-analysis-s3-member-installation",
        artifact_location=storage.mlflow_artifact_uri,
    )
    assert (
        analysis_experiment_name(storage, "sentiment")
        == "voc-sentiment-s3-member-installation"
    )


def test_additional_catalog_is_readable_without_repointing_writer(
    operations, monkeypatch, tmp_path
):
    import shutil

    from voc.analysis.store import analysis_directory, list_analyses, read_analysis

    monkeypatch.setattr(p, "_launch", perform_steps)
    first = p.execute_run(p.create_run()["run_id"])
    extra = tmp_path / "experimental-catalog"
    extra.mkdir()
    shutil.move(
        tmp_path / "analyses" / first["analysis_id"], extra / first["analysis_id"]
    )
    monkeypatch.setenv("VOC_ANALYSIS_READ_DIRS", str(extra))
    assert (
        read_analysis(first["analysis_id"]).manifest.analysis_id == first["analysis_id"]
    )
    assert query(first["analysis_id"])["state"] == "ok"
    second = p.execute_run(p.create_run()["run_id"])
    assert analysis_directory(second["analysis_id"]).parent == tmp_path / "analyses"
    assert len(list_analyses()) == 2
    shutil.copytree(
        extra / first["analysis_id"], tmp_path / "analyses" / first["analysis_id"]
    )
    with pytest.raises(ValueError, match="multiple catalogs"):
        read_analysis(first["analysis_id"])
