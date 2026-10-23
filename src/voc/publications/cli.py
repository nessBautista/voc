"""Build, activate and query local data-state publications."""

import json
from pathlib import Path

import click
from botocore.exceptions import BotoCoreError, ClientError


@click.group()
def publication():
    """Build, activate and query portable dataset-state packages."""
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
@click.option(
    "--analysis",
    "analysis_id",
    default=None,
    help="Completed analysis candidate UUID; produces a voc-data-state-v2 package. Requires --source s3.",
)
def build(source, dataset_version, output, analysis_id):
    """Export saved dataset state; does not collect, train, upload or activate."""
    import sys
    from contextlib import redirect_stdout

    from voc.datasets import get_dataset
    from voc.datasets.manifest import canonical_id

    try:
        # Validate explicit identities before touching storage or optional tools.
        canonical_id(dataset_version)
        if analysis_id is not None:
            canonical_id(analysis_id)
            if source != "s3":
                raise ValueError("An analysis package requires --source s3")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Publication destination already exists: {output}")
        import duckdb

        from .builder import build_bundle
        from .contracts import validate_description
        from .exporter import describe_analysis, describe_dataset
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
            if analysis_id is not None:
                from voc.analysis.store import read_analysis

                description = describe_analysis(read_analysis(analysis_id), description)
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
                "capabilities": manifest["capabilities"],
                "destination": str(bundle),
                "warnings": validate_description(description),
            },
            indent=2,
        )
    )


@publication.command("activate")
@click.option(
    "--bundle",
    required=True,
    type=click.Path(
        file_okay=False,
        path_type=Path,
    ),
)
@click.option(
    "--catalog",
    required=True,
    type=click.Path(
        file_okay=False,
        path_type=Path,
    ),
)
@click.option(
    "--state",
    required=True,
    type=click.Path(
        file_okay=False,
        path_type=Path,
    ),
)
def activate(bundle, catalog, state):
    """Install a validated copy and select it without changing previous versions."""
    try:
        import duckdb

        from .store import activate_bundle
    except ImportError as error:
        raise click.ClickException(
            "Install voc[publications] to activate a package"
        ) from error
    try:
        # Select only after the independent catalog copy passes validation.
        result = activate_bundle(
            bundle,
            catalog,
            state,
        )
    except (LookupError, ValueError, OSError, duckdb.Error) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            result,
            indent=2,
        )
    )


@publication.command("current")
@click.option(
    "--state",
    required=True,
    type=click.Path(
        file_okay=False,
        path_type=Path,
    ),
)
def current(state):
    """Read selection metadata; does not create storage or select a default."""
    try:
        from .store import current_publication
    except ImportError as error:
        raise click.ClickException(
            "Install voc[publications] to read publications"
        ) from error
    try:
        # A missing pointer is an error, not an empty dataset.
        result = current_publication(state)
    except (LookupError, ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            result,
            indent=2,
        )
    )


@publication.command("query")
@click.option(
    "--catalog",
    required=True,
    type=click.Path(
        file_okay=False,
        path_type=Path,
    ),
)
@click.option(
    "--publication-id",
    required=True,
    help="Explicit installed publication UUID.",
)
@click.option(
    "--kind",
    required=True,
    type=click.Choice(
        [
            "overview",
            "sources",
            "coverage",
            "freshness",
            "embeddings",
            "analysis",
            "topics",
            "sentiment",
        ]
    ),
)
def query_publication(catalog, publication_id, kind):
    """Read a named result for one fixed publication, with its evidence reference."""
    try:
        import duckdb

        from .queries import query
    except ImportError as error:
        raise click.ClickException(
            "Install voc[publications] to query publications"
        ) from error
    try:
        # Shared query functions own SQL, warnings and stable result identifiers.
        result = query(
            catalog,
            publication_id,
            kind,
        )
    except (LookupError, ValueError, OSError, duckdb.Error) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(
            result,
            indent=2,
        )
    )
