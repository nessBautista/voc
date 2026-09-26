"""Configure a local orchestrator with explicit, separated persistent storage."""

import os

from voc.paths import data_root


def bootstrap():
    # Load ML dependencies when setup is called.
    from zenml.client import Client
    from zenml.enums import StackComponentType as T
    from zenml.integrations.registry import integration_registry

    # Enable installed integrations and prepare persistent directories.
    integration_registry.activate_integrations()
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "mlflow").mkdir(exist_ok=True)
    # Client reads and writes ZenML configuration and metadata.
    client = Client()
    components = {}
    # Each tuple: component type, registered name, implementation flavor, settings.
    definitions = [
        (T.ORCHESTRATOR, "voc-local", "local", {}),
        (
            T.ARTIFACT_STORE,
            "voc-artifacts",
            "local",
            {"path": str(root / "zenml-artifacts")},
        ),
        (
            T.EXPERIMENT_TRACKER,
            "voc-mlflow",
            "mlflow",
            {
                "tracking_uri": (
                    os.environ.get("MLFLOW_TRACKING_URI")
                    or "sqlite:///" + str(root / "mlflow" / "mlflow.db")
                )
            },
        ),
    ]
    # Reuse matching components; create missing ones, but reject conflicts.
    for kind, name, flavor, config in definitions:
        try:
            item = client.get_stack_component(kind, name)
            if any(item.configuration.get(k) != v for k, v in config.items()):
                raise ValueError("Existing component configuration differs: " + name)
        except KeyError:  # This component has not been registered yet.
            item = client.create_stack_component(name, flavor, kind, config)
        components[kind] = item.id  # Collect the IDs for the stack definition.
    # A stack groups the selected components into one infrastructure configuration.
    try:
        stack = client.get_stack("voc-local")
        if any(stack.components[k][0].id != v for k, v in components.items()):
            raise ValueError("Existing voc-local stack has unexpected components")
    except KeyError:  # First setup: register the stack.
        stack = client.create_stack("voc-local", components)
    # Use this stack for subsequent pipeline runs.
    client.activate_stack(stack.id)
    from mlflow.tracking import MlflowClient

    # Use the tracking URI from the last component definition (MLflow).
    tracker_uri = definitions[-1][3]["tracking_uri"]
    tracker = MlflowClient(tracking_uri=tracker_uri)
    name = "voc-datasets"
    artifact_location = (root / "mlflow/artifacts").as_uri()
    # Prepare the experiment for later logging; this does not create a run.
    experiment = tracker.get_experiment_by_name(name)
    if experiment is None:
        tracker.create_experiment(name, artifact_location=artifact_location)
    elif experiment.artifact_location != artifact_location:
        raise ValueError(
            "MLflow experiment artifact path differs from configured storage"
        )
    # The setup CLI prints this identifier as confirmation.
    return str(stack.id)
