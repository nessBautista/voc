import json

import click

from voc import __version__


@click.group()
def main():
    """VOC framework queries."""


@main.command()
def version():
    """Print the package version."""
    click.echo(__version__)


@main.command("dataset-info")
@click.option("--version", default="latest")
def dataset_info(version):
    """Read metadata of a published workable dataset."""
    from voc.datasets import resolve
    try:
        info = resolve(version)
    except LookupError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(info, indent=2))


@main.command()
@click.option("--version", default="latest")
def summary(version):
    """Summarize a published workable dataset."""
    from voc.datasets import get_dataset, summary
    try:
        result = summary(get_dataset(version))
    except LookupError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))
