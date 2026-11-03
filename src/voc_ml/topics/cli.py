"""Commands for bounded topic fitting with explicit input identities."""

import json
import sys
from contextlib import redirect_stdout

import click


@click.group()
def topics():
    """Fit and inspect small local topic analyses."""


@topics.command("workflow")
@click.option("--dataset-version", required=True)
@click.option(
    "--embeddings", required=True, type=click.Path(exists=True, file_okay=False)
)
@click.option("--manifest-sha256", required=True)
@click.option(
    "--config",
    default="config/topics/base.toml",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False),
)
def workflow(dataset_version, embeddings, manifest_sha256, config):
    """Run G0 through the local ZenML/MLflow stack. Never chooses latest."""
    from .config import load_config
    from .production import create_run
    from .store import local_store
    from .workflow import execute_workflow

    store = local_store()
    run_id = None
    try:
        with redirect_stdout(sys.stderr):
            state = create_run(
                dataset_version,
                embeddings,
                manifest_sha256,
                load_config(config),
                store=store,
            )
            run_id = state["run_id"]
            click.echo(f"Topic producer: {run_id}", err=True)
            result = execute_workflow(run_id, store=store)
    except Exception as error:
        raise click.ClickException(f"{error}; producer={run_id}") from error
    click.echo(json.dumps(result, indent=2))


@topics.command("inspect")
@click.argument("run_id")
def inspect(run_id):
    """Read local producer state and revalidate its completed artifact."""
    from .production import inspect_run

    try:
        result = inspect_run(run_id)
    except (ValueError, KeyError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))
