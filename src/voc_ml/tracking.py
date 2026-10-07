"""One MLflow configuration for setup, pipeline steps and the local server."""

import os


def tracking_uri(storage):
    """Metadata stays in SQLite unless an explicit tracking endpoint is supplied."""
    return os.environ.get("MLFLOW_TRACKING_URI") or (
        "sqlite:///" + str(storage.runtime_root / "mlflow/mlflow.db")
    )


def experiment_name(storage):
    # A new experiment preserves the artifact location of existing local runs.
    if storage.artifact_destination == "s3":
        return "voc-datasets-s3-" + storage.installation_id
    return "voc-datasets"


def ensure_experiment(client, storage):
    """Create or reuse the intended experiment; never repoint an existing one."""
    return _ensure_experiment(client, storage, experiment_name(storage))


def embedding_experiment_name(storage):
    return experiment_name(storage).replace("voc-datasets", "voc-embeddings", 1)


def ensure_embedding_experiment(client, storage):
    """Embedding reports use their own experiment, with the configured artifact root."""
    return _ensure_experiment(client, storage, embedding_experiment_name(storage))


def _ensure_experiment(client, storage, name):
    experiment = client.get_experiment_by_name(name)
    if experiment is None:
        return client.create_experiment(
            name, artifact_location=storage.mlflow_artifact_uri
        )
    if experiment.lifecycle_stage != "active":
        raise ValueError("Configured MLflow experiment is deleted: " + name)
    if experiment.artifact_location.rstrip("/") != storage.mlflow_artifact_uri.rstrip(
        "/"
    ):
        raise ValueError("MLflow experiment artifact path differs: " + name)
    return experiment.experiment_id


def server_command(python, storage):
    # The local UI reads local metadata. Clients upload files directly to storage.
    return [
        python,
        "-m",
        "mlflow",
        "server",
        "--backend-store-uri",
        "sqlite:///" + str(storage.runtime_root / "mlflow/mlflow.db"),
        "--no-serve-artifacts",
        "--default-artifact-root",
        storage.mlflow_artifact_uri,
        "--host",
        "0.0.0.0",
        "--port",
        "5000",
        "--workers",
        "1",
    ]
