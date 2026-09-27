import json

import click
from botocore.exceptions import BotoCoreError, ClientError

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
@click.option("--source", type=click.Choice(["local", "s3"]), default=None)
def dataset_info(version, source):
    """Read metadata of a published workable dataset."""
    from voc.datasets import resolve

    try:
        info = resolve(version, **({"source": source} if source else {}))
    except (LookupError, ValueError, OSError, BotoCoreError, ClientError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(info, indent=2))


@main.command()
@click.option("--version", default="latest")
@click.option("--source", type=click.Choice(["local", "s3"]), default=None)
def summary(version, source):
    """Summarize a published workable dataset."""
    from voc.datasets import get_dataset, summary

    try:
        result = summary(get_dataset(version, **({"source": source} if source else {})))
    except (LookupError, ValueError, OSError, BotoCoreError, ClientError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))
