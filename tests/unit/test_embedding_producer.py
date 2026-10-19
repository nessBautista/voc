"""Producer safety boundaries: checked artifacts, durable failures and CLI results."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from voc.datasets.manifest import file_hash, frame_hash
from voc.embeddings.contracts import canonical_json
from voc.models import Snapshot
from voc_ml.cli import main
from voc_ml.embeddings import (
    FeatureConfig,
    encode_features,
    prepare_features,
    producer,
    select_snapshot,
)
from voc_ml.embeddings.artifacts import read_encoding, read_input, write_input


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def outputs(runtime, monkeypatch):
    from voc_ml.embeddings import sentence_encoder

    frame = pd.DataFrame(
        {"record_id": ["a", "b", "c"], "model_text": ["good", "bad", "good"]}
    )
    pinned = select_snapshot(
        Snapshot(
            frame,
            {
                "release_id": "b7a61a8b-27d8-46a7-b2ce-67d97268b741",
                "source": "s3",
                "stage": "prepared",
                "schema_version": "workable-v1",
                "dataset_uri": "s3://test-bucket/voc/datasets",
                "row_count": 3,
                "file_sha256": "a" * 64,
                "content_sha256": frame_hash(frame),
            },
        )
    )

    class Encoder:
        def __init__(self, profile, *args, **kwargs):
            self.resolved_revision = profile.revision

        def encode(self, texts, batch_size):
            vectors = np.zeros((len(texts), 384), dtype="<f4")
            vectors[:, 0] = 1
            return vectors, [3] * len(texts)

    monkeypatch.setattr(sentence_encoder, "SentenceEncoder", Encoder)
    state = producer.create_run()
    folder = Path(state["paths"]["run"])
    source = write_input(pinned, folder / "input")
    features = prepare_features(pinned, FeatureConfig(), folder / "features")
    encoded = encode_features(
        features,
        output_dir=folder / "encoding",
        cache_path=runtime / "cache.db",
        model_dir=runtime / "models",
    )
    producer.update_run(
        state["producer_run_id"],
        input_reference=source,
        encoding_reference=encoded,
        zenml_run_id="test-zenml",
        mlflow_run_id="test-mlflow",
    )
    return source, encoded, state["producer_run_id"]


def test_verified_local_artifacts_and_duplicate_rows(outputs):
    source, encoded, _ = outputs
    assert read_input(source).reviews.record_id.tolist() == ["a", "b", "c"]
    report = read_encoding(
        encoded, expected_input=source, expected_profile="minilm-verbatim-v1"
    )
    assert report["rows"] == 3 and report["unique_texts"] == 2


@pytest.mark.parametrize(
    "artifact", ["reviews.parquet", "vectors.npy", "encoding-report.json"]
)
def test_changed_encoding_bytes_are_rejected(outputs, artifact):
    _, encoded, _ = outputs
    path = Path(encoded["directory"]) / artifact
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum"):
        read_encoding(encoded)


def test_input_changed_after_encoding_is_rejected(outputs):
    source, encoded, _ = outputs
    path = Path(source["directory"]) / "reviews.parquet"
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Pinned input checksum"):
        read_encoding(encoded, expected_input=source)


@pytest.mark.parametrize(
    "mutation",
    ["order", "dtype", "shape", "nonfinite", "normalization", "count", "profile"],
)
def test_incompatible_artifacts_with_updated_checksums_fail(outputs, mutation):
    _, reference, _ = outputs
    directory = Path(reference["directory"])
    report_path = directory / "encoding-report.json"
    report = json.loads(report_path.read_bytes())
    if mutation == "order":
        path = directory / "reviews.parquet"
        pd.read_parquet(path).iloc[::-1].to_parquet(path, index=False)
    elif mutation in {"dtype", "shape", "nonfinite", "normalization"}:
        path = directory / "vectors.npy"
        values = np.load(path)
        if mutation == "dtype":
            values = values.astype("float64")
        elif mutation == "shape":
            values = values[:-1]
        elif mutation == "nonfinite":
            values[0, 0] = np.nan
        else:
            values[0, 0] = 2
        np.save(path, values, allow_pickle=False)
    elif mutation == "count":
        report["reused_unique"] += 1
    else:
        report["profile"]["config"]["max_length"] += 1
    for name in report["artifacts"]:
        path = directory / name
        report["artifacts"][name] = {
            "sha256": file_hash(path),
            "size_bytes": path.stat().st_size,
        }
    report_path.write_bytes(canonical_json(report))
    reference["report_sha256"] = file_hash(report_path)
    with pytest.raises(ValueError):
        read_encoding(reference)


@pytest.mark.parametrize(
    "error", [RuntimeError("tracking failed"), KeyboardInterrupt(), SystemExit(1)]
)
def test_failed_launch_does_not_complete_even_with_encoding(
    outputs, monkeypatch, error
):
    _, _, run_id = outputs
    monkeypatch.setattr(producer, "_launch", Mock(side_effect=error))
    state = producer.execute_run(run_id)
    assert state["state"] == "failed" and "completed_at" not in state
    assert state["error"]["type"] == type(error).__name__
    assert producer.inspect_run(run_id)["state"] == "failed"
    with pytest.raises(ValueError, match="already launched"):
        producer.execute_run(run_id)


def test_unfinished_record_is_never_treated_as_completed(runtime):
    state = producer.create_run()
    assert producer.inspect_run(state["producer_run_id"])["state"] == "running"
    response = CliRunner().invoke(
        main, ["embeddings", "inspect", state["producer_run_id"], "--json"]
    )
    assert response.exit_code == 1
    assert json.loads(response.stdout)["state"] == "running"


def test_post_tracking_validation_failure_is_not_completed(outputs, monkeypatch):
    _, encoded, run_id = outputs

    def launch(state):
        (Path(encoded["directory"]) / "vectors.npy").write_bytes(b"bad")
        return producer.load_run(run_id)

    monkeypatch.setattr(producer, "_launch", launch)
    assert producer.execute_run(run_id)["state"] == "failed"


def test_inspect_rechecks_completed_artifacts(outputs, monkeypatch):
    _, encoded, run_id = outputs
    monkeypatch.setattr(producer, "_launch", lambda state: producer.load_run(run_id))
    assert producer.execute_run(run_id)["state"] == "completed"
    assert producer.inspect_run(run_id)["state"] == "completed"
    (Path(encoded["directory"]) / "vectors.npy").write_bytes(b"bad")
    response = CliRunner().invoke(main, ["embeddings", "inspect", run_id, "--json"])
    assert response.exit_code == 1
    assert json.loads(response.stdout)["state"] == "error"


@pytest.mark.parametrize(
    "args",
    [
        ["--limit", "0"],
        ["--batch-size", "-1"],
        ["--profile", "unknown"],
        ["--source", "local"],
        ["--dataset-version", "bad-id"],
        ["--unknown"],
        ["--limit", "3", "--limit=4"],
    ],
)
def test_invalid_cli_options_have_json_errors_without_run(runtime, args):
    result = CliRunner().invoke(main, ["embeddings", "workflow", *args, "--json"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["state"] == "error"
    assert not (runtime / "embeddings").exists()


def test_failed_cli_keeps_id_and_logs_off_json_stdout(runtime, monkeypatch):
    def fail(state):
        print("orchestration diagnostic")
        raise RuntimeError("tracking unavailable")

    monkeypatch.setattr(producer, "_launch", fail)
    result = CliRunner().invoke(
        main, ["embeddings", "workflow", "--limit", "3", "--json"]
    )
    state = json.loads(result.stdout)
    assert result.exit_code == 1 and state["state"] == "failed"
    assert producer.load_run(state["producer_run_id"])["state"] == "failed"
    assert "orchestration diagnostic" in result.stderr


def test_embedding_experiment_does_not_repoint_dataset_experiment():
    from voc_ml.tracking import ensure_embedding_experiment, ensure_experiment

    client = Mock()
    client.get_experiment_by_name.return_value = None
    settings = SimpleNamespace(
        artifact_destination="local", mlflow_artifact_uri="file:///tmp/reports"
    )
    ensure_experiment(client, settings)
    ensure_embedding_experiment(client, settings)
    assert [call.args[0] for call in client.create_experiment.call_args_list] == [
        "voc-datasets",
        "voc-embeddings",
    ]
    client.get_experiment_by_name.return_value = SimpleNamespace(
        lifecycle_stage="active", artifact_location="file:///somewhere-else"
    )
    with pytest.raises(ValueError, match="artifact path differs"):
        ensure_embedding_experiment(client, settings)
