import json

import click
from botocore.exceptions import BotoCoreError, ClientError

from voc import __version__
from voc.analysis.cli import analysis_cli
from voc.publications.cli import publication


@click.group()
def main():
    """VOC dataset, analysis and publication tools."""
    # Click dispatches the selected command registered below.


@main.command()
def version():
    # Report the package version without loading a dataset.
    click.echo(__version__)


@main.command("storage-info")
@click.option(
    "--config",
    default=None,
    type=click.Path(
        exists=True,
        dir_okay=False,
    ),
)
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
        # Load settings with the optional configuration and destination overrides.
        settings = load_storage(
            config,
            destination=destination,
        )
    except (
        ValueError,
        OSError,
    ) as error:
        # Turn expected configuration failures into a readable CLI error.
        raise click.ClickException(str(error)) from error

    # Display configuration and paths; credentials are not part of this report.
    click.echo(
        json.dumps(
            settings.describe(),
            indent=2,
        )
    )


@main.command("s3-check")
@click.option(
    "--live",
    is_flag=True,
)
def s3_check(live):
    # Require explicit consent because this check writes a real S3 object.
    if not live:
        raise click.UsageError("Use --live to write a personal test object")
    import json

    from voc.storage.diagnostics import check_s3

    # Write and read back a unique personal test object, then print the report.
    click.echo(
        json.dumps(
            check_s3(),
            indent=2,
        )
    )


@main.command("dataset-info")
@click.option(
    "--version",
    default="latest",
)
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
        # Resolve metadata; omitting source preserves the configured default.
        info = resolve(
            version,
            **({"source": source} if source else {}),
        )
    except (
        LookupError,
        ValueError,
        OSError,
        BotoCoreError,
        ClientError,
    ) as error:
        # Present expected lookup, validation and storage failures as CLI errors.
        raise click.ClickException(str(error)) from error

    # Print publication metadata as indented JSON.
    click.echo(
        json.dumps(
            info,
            indent=2,
        )
    )


@main.command()
@click.option(
    "--version",
    default="latest",
)
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
        # Load the selected dataset, then calculate source/month counts.
        # An omitted source keeps the default from storage settings.
        result = summary(
            get_dataset(
                version,
                **({"source": source} if source else {}),
            )
        )
    except (
        LookupError,
        ValueError,
        OSError,
        BotoCoreError,
        ClientError,
    ) as error:
        # Convert expected dataset and storage failures into a readable CLI error.
        raise click.ClickException(str(error)) from error

    # Print the computed counts and dataset metadata as indented JSON.
    click.echo(
        json.dumps(
            result,
            indent=2,
        )
    )


class EmbeddingCommands(click.Group):
    def parse_args(self, ctx, args):
        # Remember JSON mode before parsing, so argument errors can use it too.
        ctx.meta["embedding_json"] = "--json" in args
        return super().parse_args(
            ctx,
            args,
        )

    def invoke(self, ctx):
        try:
            # Let Click run the selected embeddings command.
            return super().invoke(ctx)
        except click.ClickException as error:
            # Keep Click's normal text errors when JSON was not requested.
            if not ctx.meta.get("embedding_json"):
                raise

            # Emit a machine-readable error while preserving its exit code.
            click.echo(
                json.dumps(
                    {
                        "state": "error",
                        "error": error.format_message(),
                    }
                )
            )
            ctx.exit(error.exit_code)


@main.group(cls=EmbeddingCommands)
def embeddings():
    """Consume and fully validate stored embedding releases."""
    # Group consumer commands under `voc embeddings`.


@embeddings.command("inspect")
@click.option(
    "--dataset-version",
    required=True,
    help="Workable dataset release UUID, or latest.",
)
@click.option(
    "--version",
    default="latest",
    show_default=True,
    help="Embedding release UUID, or latest full release.",
)
@click.option(
    "--profile",
    default="minilm-verbatim-v1",
    show_default=True,
)
@click.option(
    "--source",
    type=click.Choice(["s3"]),
    default="s3",
)
@click.option(
    "--allow-subset",
    is_flag=True,
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
)
def embeddings_inspect(
    dataset_version,
    version,
    profile,
    source,
    allow_subset,
    as_json,
):
    """Download/cache and fully validate embeddings, then show metadata only."""
    import sys
    from contextlib import redirect_stdout

    from voc import collector, get_embeddings
    from voc.embeddings.contracts import require_uuid
    from voc.embeddings.profiles import resolve_profile

    try:
        # Validate explicit release IDs and the profile before fetching data.
        if dataset_version != "latest":
            require_uuid(
                dataset_version,
                "dataset_version",
            )
        if version != "latest":
            require_uuid(
                version,
                "embedding_release_id",
            )
        resolve_profile(profile)

        # Send incidental output to stderr to keep stdout valid JSON.
        with redirect_stdout(sys.stderr):
            # Load the prepared dataset used to verify embedding alignment.
            dataset = collector.get_dataset(
                stage="prepared",
                version=dataset_version,
                source=source,
            )
            # Read and validate stored vectors; this does not run an ML model.
            bundle = get_embeddings(
                dataset=dataset,
                profile=profile,
                version=version,
                source=source,
                allow_subset=allow_subset,
            )
    except (
        LookupError,
        ValueError,
        OSError,
        BotoCoreError,
        ClientError,
    ) as error:
        # The command group selects a text or JSON representation of this error.
        raise click.ClickException(str(error)) from error

    # Report verified metadata, without printing vectors or review text.
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


# Register portable publication commands alongside the existing dataset tools.
main.add_command(publication)
main.add_command(analysis_cli)


if __name__ == "__main__":
    main()
