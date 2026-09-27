"""Experiment isolation and artifact round trips; no AWS credentials required."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mlflow.tracking import MlflowClient
from test_storage_bootstrap import storage

from src.mlops.check_mlflow import CONTENT, create_check, verify_run
from src.mlops.tracking import (
    ensure_experiment,
    experiment_name,
    server_command,
    tracking_uri,
)


def test_destinations_use_separate_experiments_and_keep_metadata_local(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    local, remote = storage(tmp_path, "local"), storage(tmp_path, "s3")
    assert experiment_name(local) == "voc-datasets"
    assert experiment_name(remote) == "voc-datasets-s3-" + remote.installation_id
    assert (
        tracking_uri(local)
        == tracking_uri(remote)
        == "sqlite:///" + str(tmp_path / "mlflow/mlflow.db")
    )
    command = server_command("python", remote)
    assert "--no-serve-artifacts" in command
    assert (
        command[command.index("--default-artifact-root") + 1]
        == remote.mlflow_artifact_uri
    )
    assert command[command.index("--backend-store-uri") + 1] == tracking_uri(remote)


def test_s3_experiment_registration_and_reuse(tmp_path):
    client = Mock()
    settings = storage(tmp_path, "s3")
    client.get_experiment_by_name.return_value = None
    ensure_experiment(client, settings)
    client.create_experiment.assert_called_once_with(
        experiment_name(settings), artifact_location=settings.mlflow_artifact_uri
    )
    client.reset_mock()
    client.get_experiment_by_name.return_value = SimpleNamespace(
        lifecycle_stage="active",
        artifact_location=settings.mlflow_artifact_uri,
        experiment_id="existing",
    )
    assert ensure_experiment(client, settings) == "existing"
    client.create_experiment.assert_not_called()


@pytest.mark.parametrize("deleted", [False, True])
def test_conflicting_or_deleted_experiment_is_rejected(tmp_path, deleted):
    client = Mock()
    settings = storage(tmp_path, "s3")
    client.get_experiment_by_name.return_value = SimpleNamespace(
        lifecycle_stage="deleted" if deleted else "active",
        artifact_location=settings.mlflow_artifact_uri
        if deleted
        else "s3://another/location",
    )
    with pytest.raises(ValueError):
        ensure_experiment(client, settings)
    client.create_experiment.assert_not_called()


def test_real_local_file_round_trip_and_pinned_reload(tmp_path, monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    settings = storage(tmp_path, "local")
    (tmp_path / "mlflow").mkdir()
    client = MlflowClient(tracking_uri=tracking_uri(settings))
    first = create_check(client, settings)
    second = create_check(client, settings)
    assert first["run_id"] != second["run_id"]
    assert verify_run(client, first["run_id"], settings) == first
    assert first["size_bytes"] == len(CONTENT)
    assert first["artifact_uri"].startswith(settings.mlflow_artifact_uri + "/")
    assert len(client.search_runs([ensure_experiment(client, settings)])) == 2
    # Changing the destination cannot turn the previous check into an S3 success.
    with pytest.raises(ValueError, match="different MLflow experiment"):
        verify_run(
            client,
            first["run_id"],
            replace(settings, artifact_destination="s3", installation_id="other"),
        )


def test_failed_upload_is_marked_failed(tmp_path):
    client = Mock()
    client.get_experiment_by_name.return_value = None
    client.create_run.return_value.info.run_id = "failed-run"
    client.log_artifact.side_effect = OSError("simulated upload failure")
    with pytest.raises(OSError, match="simulated upload"):
        create_check(client, storage(tmp_path, "local"))
    client.set_terminated.assert_called_once_with("failed-run", status="FAILED")
