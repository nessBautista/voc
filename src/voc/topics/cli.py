"""Read stored topics without model, workflow or remote-service calls."""

import json

import click

from .reading import read_topics


@click.group()
def topics():
    """Inspect and read portable local topic analyses."""


@topics.command("read")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--manifest-sha256", required=True)
@click.option("--topic-id", type=int, default=None)
def read(directory, manifest_sha256, topic_id):
    """Verify the complete artifact and optionally return one topic's evidence."""
    try:
        bundle = read_topics(directory, manifest_sha256=manifest_sha256)
        result = bundle.info if topic_id is None else bundle.topic(topic_id)
    except (ValueError, LookupError, TypeError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
