"""Operator commands for building local data-state publications."""

import json
from pathlib import Path

import click
from botocore.exceptions import BotoCoreError, ClientError


@click.group()
def publication():
    """Build portable packages from existing datasets."""
    # Keep publication operations separate from dataset/model producers.


@publication.command("build")
@click.option(
    "--source",
    type=click.Choice(["personal", "s3"]),
    required=True,
)
@click.option(
    "--dataset-version",
    required=True,
    help="Explicit S3 release UUID or personal artifact UUID; latest is not accepted.",
)
@click.option(
    "--output",
    required=True,
    type=click.Path(
        file_okay=False,
        path_type=Path,
    ),
    help="New local directory, for example /workspace/data/data-state-demo/real-a.",
)
def build(source, dataset_version, output):
    """Export saved dataset state; does not collect, train, upload or activate."""
    import sys
    from contextlib import redirect_stdout

    from voc.datasets import get_dataset
    from voc.datasets.manifest import canonical_id

    try:
        # Validate explicit identities before touching storage or optional tools.
        canonical_id(dataset_version)
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Publication destination already exists: {output}")
        import duckdb

        from .builder import build_bundle
        from .contracts import validate_description
        from .exporter import describe_dataset
        from .store import validate_bundle
    except ImportError as error:
        raise click.ClickException(
            "Install voc[publications] to build a package"
        ) from error
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error

    try:
        # Keep incidental reader output off the machine-readable JSON response.
        with redirect_stdout(sys.stderr):
            snapshot = get_dataset(
                dataset_version,
                source=source,
            )
            description = describe_dataset(snapshot)
            if (
                description["dataset"]["source_kind"] != source
                or description["dataset"]["source_version"] != dataset_version
            ):
                raise ValueError(
                    "Loaded dataset identity differs from the requested version"
                )
            bundle = build_bundle(
                description,
                output,
            )
            manifest = validate_bundle(bundle)
    except (
        LookupError,
        ValueError,
        OSError,
        BotoCoreError,
        ClientError,
        duckdb.Error,
    ) as error:
        raise click.ClickException(str(error)) from error

    # Report identity and limitations without exposing the source's private metadata.
    click.echo(
        json.dumps(
            {
                "publication_id": manifest["publication_id"],
                "dataset_id": manifest["dataset_id"],
                "schema_version": manifest["schema_version"],
                "destination": str(bundle),
                "warnings": validate_description(description),
            },
            indent=2,
        )
    )
