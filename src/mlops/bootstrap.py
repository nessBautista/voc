"""Configure a local orchestrator with explicit, separated persistent storage."""

from voc.storage import load_storage

from .tracking import ensure_experiment, tracking_uri


def zenml_store_settings(storage):
    """Keep local names stable; give each S3 installation separate registrations."""
    if storage.artifact_destination == "local":
        return (
            "voc-local",
            "voc-artifacts",
            "local",
            {"path": storage.zenml_artifact_uri},
        )
    suffix = storage.installation_id
    return (
        f"voc-s3-{suffix}",
        f"voc-artifacts-s3-{suffix}",
        "s3",
        {
            "path": storage.zenml_artifact_uri,
            "client_kwargs": {"region_name": storage.region},
        },
    )


def ensure_component(client, kind, name, flavor, config):
    """Reuse exact intended settings; never silently repoint an old component."""
    try:
        item = client.get_stack_component(kind, name)
    except KeyError:
        return client.create_stack_component(name, flavor, kind, config)
    if item.flavor_name != flavor or any(
        item.configuration.get(k) != v for k, v in config.items()
    ):
        raise ValueError("Existing component configuration differs: " + name)
    if flavor == "s3" and any(
        item.configuration.get(k)
        for k in ("key", "secret", "token", "authentication_secret")
    ):
        raise ValueError("S3 component must use environment credentials: " + name)
    return item


def bootstrap():
    # Load ML dependencies when setup is called.
    from zenml.client import Client
    from zenml.enums import StackComponentType as T
    from zenml.integrations.registry import integration_registry

    # Enable installed integrations and prepare persistent directories.
    integration_registry.activate_integrations()
    storage = load_storage()
    root = storage.runtime_root
    stack_name, store_name, store_flavor, store_config = zenml_store_settings(storage)
    root.mkdir(parents=True, exist_ok=True)
    (root / "mlflow").mkdir(exist_ok=True)
    # Client reads and writes ZenML configuration and metadata.
    client = Client()
    components = {}
    # Each tuple: component type, registered name, implementation flavor, settings.
    definitions = [
        (T.ORCHESTRATOR, "voc-local", "local", {}),
        # S3 registration stores only its URI/region; SDK credentials come from env.
        (T.ARTIFACT_STORE, store_name, store_flavor, store_config),
        (
            T.EXPERIMENT_TRACKER,
            "voc-mlflow",
            "mlflow",
            {"tracking_uri": tracking_uri(storage)},
        ),
    ]
    # Reuse matching components; create missing ones, but reject conflicts.
    for kind, name, flavor, config in definitions:
        item = ensure_component(client, kind, name, flavor, config)
        components[kind] = item.id  # Collect the IDs for the stack definition.
    # A stack groups the selected components into one infrastructure configuration.
    try:
        stack = client.get_stack(stack_name)
        if any(
            not stack.components.get(k) or stack.components[k][0].id != v
            for k, v in components.items()
        ):
            raise ValueError("Existing stack has unexpected components: " + stack_name)
    except KeyError:  # First setup: register the stack.
        stack = client.create_stack(stack_name, components)
    from mlflow.tracking import MlflowClient

    # Use the tracking URI from the last component definition (MLflow).
    tracker_uri = definitions[-1][3]["tracking_uri"]
    tracker = MlflowClient(tracking_uri=tracker_uri)
    # Creating an experiment records its artifact URI; it creates no run or S3 file.
    ensure_experiment(tracker, storage)
    # Activate only after component and experiment validation succeeds.
    client.activate_stack(stack.id)
    # The setup CLI prints this identifier as confirmation.
    return str(stack.id)
