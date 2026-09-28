import json
from botocore.exceptions import BotoCoreError, ClientError
import click
from voc import __version__

@click.group()
def main():
    """VOC dataset tools."""

@main.command()
def version():
    click.echo(__version__)


@main.command("storage-info")
@click.option("--config", default=None, type=click.Path(exists=True, dir_okay=False))
@click.option("--destination", type=click.Choice(["local", "s3"]), default=None)
def storage_info(config, destination):
    """Inspect intended storage settings; does not reconfigure ZenML or MLflow."""
    import json
    from voc.storage import load_storage

    try:
        settings = load_storage(config, destination=destination)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    # Display configuration and paths; credentials are not part of this report.
    click.echo(json.dumps(settings.describe(), indent=2))

@main.command("s3-check")
@click.option("--live", is_flag=True)
def s3_check(live):
    if not live:
        raise click.UsageError("Use --live to write a personal test object")
    import json
    from voc.storage.diagnostics import check_s3
    click.echo(json.dumps(check_s3(), indent=2))

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
