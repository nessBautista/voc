"""Execute a frozen two-system comparison through existing producer workflows."""

import json
import sys
from contextlib import redirect_stdout

import click

from . import operations


@click.group()
def experiment():
    """Prepare and run the bounded diffusion comparison."""


@experiment.command("prepare")
@click.option("--local-inputs", required=True, type=click.Path(exists=True))
@click.option("--annotations", required=True, type=click.Path(exists=True))
@click.option("--relevance-model", required=True, type=click.Path(exists=True))
@click.option("--relevance-sha256", required=True)
@click.option("--output", required=True, type=click.Path())
@click.option("--start", default="2026-07-01", show_default=True)
@click.option("--end", default="2026-09-01", show_default=True)
@click.option("--size", default=1000, type=click.IntRange(min=20))
@click.option("--seed", default=42, type=click.IntRange(min=0))
def prepare(**kwargs):
    try:
        result = operations.prepare(**kwargs)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


@experiment.command("run")
@click.argument("system", type=click.Choice(["base", "alternative"]))
@click.option("--directory", required=True, type=click.Path(exists=True))
def run(system, directory):
    try:
        with redirect_stdout(sys.stderr):
            result = operations.run(directory, system)
    except Exception as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


@experiment.command("inspect")
@click.option("--directory", required=True, type=click.Path(exists=True))
def inspect(directory):
    click.echo(json.dumps(operations.load_protocol(directory), indent=2))
