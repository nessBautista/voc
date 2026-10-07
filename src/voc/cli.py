import json

import click
from botocore.exceptions import BotoCoreError, ClientError

from voc import __version__


@click.group()
def main():
    """VOC dataset tools."""


@main.command()
def version():
    click.echo(__version__)


@main.command("storage-info")
@click.option("--config", default=None, type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--destination",
    type=click.Choice(["personal", "s3", "local"]),
    default=None,
    help="Personal catalog or shared S3 releases; local is a compatibility alias.",
)
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
@click.option(
    "--source",
    type=click.Choice(["personal", "s3", "local"]),
    default=None,
    help="Personal catalog or shared S3 releases; local is a compatibility alias.",
)
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
@click.option(
    "--source",
    type=click.Choice(["personal", "s3", "local"]),
    default=None,
    help="Personal catalog or shared S3 releases; local is a compatibility alias.",
)
def summary(version, source):
    """Summarize a published workable dataset."""
    from voc.datasets import get_dataset, summary

    try:
        result = summary(get_dataset(version, **({"source": source} if source else {})))
    except (LookupError, ValueError, OSError, BotoCoreError, ClientError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


class EmbeddingCommands(click.Group):
    def parse_args(self, ctx, args):
        ctx.meta["embedding_json"] = "--json" in args
        return super().parse_args(ctx, args)

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except click.ClickException as error:
            if not ctx.meta.get("embedding_json"):
                raise
            click.echo(json.dumps({"state": "error", "error": error.format_message()}))
            ctx.exit(error.exit_code)


@main.group(cls=EmbeddingCommands)
def embeddings():
    """Consume and fully validate stored embedding releases."""


@embeddings.command("inspect")
@click.option(
    "--dataset-version", required=True, help="Workable dataset release UUID, or latest."
)
@click.option(
    "--version",
    default="latest",
    show_default=True,
    help="Embedding release UUID, or latest full release.",
)
@click.option("--profile", default="minilm-verbatim-v1", show_default=True)
@click.option("--source", type=click.Choice(["s3"]), default="s3")
@click.option("--allow-subset", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
def embeddings_inspect(
    dataset_version, version, profile, source, allow_subset, as_json
):
    """Download/cache and fully validate embeddings, then show metadata only."""
    import sys
    from contextlib import redirect_stdout

    from voc import collector, get_embeddings
    from voc.embeddings.contracts import require_uuid
    from voc.embeddings.profiles import resolve_profile

    try:
        if dataset_version != "latest":
            require_uuid(dataset_version, "dataset_version")
        if version != "latest":
            require_uuid(version, "embedding_release_id")
        resolve_profile(profile)
        with redirect_stdout(sys.stderr):
            dataset = collector.get_dataset(
                stage="prepared", version=dataset_version, source=source
            )
            bundle = get_embeddings(
                dataset=dataset,
                profile=profile,
                version=version,
                source=source,
                allow_subset=allow_subset,
            )
    except (LookupError, ValueError, OSError, BotoCoreError, ClientError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            {
                "state": "verified",
                "artifact_content_verified": True,
                "release": bundle.info,
            },
            indent=None if as_json else 2,
        )
    )


if __name__ == "__main__":
    main()
