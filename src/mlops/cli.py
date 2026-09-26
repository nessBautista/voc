import os
from pathlib import Path
import tomllib
import uuid
import click


def environment():
    from voc.paths import data_root
    os.environ.setdefault("VOC_DATA_DIR", str(data_root()))
    os.environ.setdefault("ZENML_CONFIG_PATH", str(data_root() / "zenml-client"))
    os.environ.setdefault("ZENML_CUSTOM_SOURCE_ROOT", str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("ZENML_ANALYTICS_OPT_IN", "false")


@click.group()
def main():
    """ML project workflows."""
    environment()


@main.command()
def setup():
    """Select the local stack."""
    from .bootstrap import bootstrap
    click.echo(bootstrap())


@main.group()
def dataset():
    """Manage the ML dataset workflow."""


@dataset.command("init")
@click.option("--seed", type=click.Path(exists=True), envvar="VOC_SEED_PATH", required=True)
@click.option("--config", default="config/collector.toml")
def initialize(seed, config):
    from voc.collector import initialize_raw_store, load_config
    cfg = load_config(config)
    click.echo(initialize_raw_store(cfg["raw_store"], seed_path=seed))


@dataset.command("run")
@click.option("--config", default="config/collector.toml")
@click.option("--rules", default="config/preparation.toml")
@click.option("--refresh/--no-refresh", default=False)
@click.option("--live", is_flag=True)
def run(config, rules, refresh, live):
    if refresh and not live:
        raise click.UsageError("Use --refresh --live to make store requests")
    from voc.collector import load_config
    from .pipeline import dataset_pipeline
    with open(rules, "rb") as stream:
        settings = tomllib.load(stream)
    result = dataset_pipeline(config=load_config(config), rules=settings,
                              refresh=refresh, operation_id=str(uuid.uuid4()),
                              project_identity="tutorial-lesson-06")
    click.echo(str(result.id))


if __name__ == "__main__":
    main()
