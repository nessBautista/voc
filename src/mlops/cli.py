"""Click commands for the ML project; deliberately separate from the VOC CLI."""

import json
import os
import tomllib
import uuid
from pathlib import Path

import click


def environment():
    from voc.paths import data_root

    os.environ.setdefault("VOC_DATA_DIR", str(data_root()))
    os.environ.setdefault("ZENML_CONFIG_PATH", str(data_root() / "zenml-client"))
    os.environ.setdefault(
        "ZENML_CUSTOM_SOURCE_ROOT", str(Path(__file__).resolve().parents[2])
    )
    os.environ.setdefault("ZENML_ANALYTICS_OPT_IN", "false")


@click.group()
def main():
    """ML environment, collection/preparation pipelines and publication."""
    environment()


@main.command()
def setup():
    """Register and select the configured ZenML stack."""
    from .bootstrap import bootstrap

    click.echo(bootstrap())


@main.command("storage-info")
@click.option("--config", default=None, type=click.Path(exists=True, dir_okay=False))
@click.option("--destination", type=click.Choice(["local", "s3"]), default=None)
def storage_info(config, destination):
    """Inspect intended storage settings; does not reconfigure ZenML or MLflow."""
    from voc.storage import load_storage

    try:
        settings = load_storage(config, destination=destination)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(settings.describe(), indent=2))


@main.group()
def dataset():
    """Manage the ML dataset workflow."""


@dataset.command("init")
@click.option(
    "--seed", type=click.Path(exists=True), envvar="VOC_SEED_PATH", required=True
)
@click.option("--config", default="config/collector.toml")
def initialize(seed, config):
    from voc.collector import initialize_raw_store, load_config

    cfg = load_config(config)
    click.echo(initialize_raw_store(cfg["raw_store"], seed_path=seed))


@dataset.command("run")
@click.option("--config", default="config/collector.toml")
@click.option("--rules", default="config/preparation.toml")
@click.option(
    "--refresh/--no-refresh",
    default=False,
    help="Refresh requires --live acknowledgement.",
)
@click.option("--live", is_flag=True, help="Allow real store requests.")
@click.option("--operation-id", default=None)
def run(config, rules, refresh, live, operation_id):
    if refresh and not live:
        raise click.UsageError("Use --refresh --live to make store requests")
    from voc.collector import load_config

    from .pipeline import dataset_pipeline
    from .runner import code_identity, publish_run

    cfg = load_config(config)
    with open(rules, "rb") as f:
        settings = tomllib.load(f)
    result = dataset_pipeline(
        config=cfg,
        rules=settings,
        refresh=refresh,
        operation_id=operation_id or str(uuid.uuid4()),
        project_identity=code_identity(),
    )
    if result is None:
        raise click.ClickException("No completed run returned")
    click.echo(json.dumps(publish_run(str(result.id)), indent=2))


@dataset.command("publish")
@click.argument("run_id")
def publish(run_id):
    """Retry publication of an already-completed dataset run."""
    from .runner import publish_run

    click.echo(json.dumps(publish_run(run_id), indent=2))


if __name__ == "__main__":
    main()
