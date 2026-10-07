"""Developer utilities independent of model and orchestration dependencies."""

import json
import sys
from contextlib import redirect_stdout

import click
from click.core import ParameterSource

from voc.storage.inspection import inspect_storage, validate_selectors
from voc.storage.settings import load_storage

from .storage_render import render_report


class DeveloperCommands(click.Group):
    """Keep JSON errors machine-readable, including command parsing errors."""

    def parse_args(self, ctx, args):
        ctx.meta["json_requested"] = "--json" in args
        return super().parse_args(ctx, args)

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except click.ClickException as error:
            if not ctx.meta.get("json_requested"):
                raise
            click.echo(json.dumps({"state": "error", "error": error.format_message()}))
            error.show(file=sys.stderr)
            ctx.exit(error.exit_code)


@click.group(cls=DeveloperCommands)
def main():
    """VOC developer diagnostics."""


@main.group()
def storage():
    """Read-only storage inspection."""


@storage.command("inspect")
@click.option(
    "--scope",
    type=click.Choice(["all", "embeddings", "datasets", "personal"]),
    default="all",
    show_default=True,
)
@click.option("--dataset-version", default=None)
@click.option("--profile", default=None)
@click.option("--release", default=None)
@click.option("--limit", type=click.IntRange(1, 1000), default=100, show_default=True)
@click.option("--depth", type=click.IntRange(1, 10), default=2, show_default=True)
@click.option("--continuation-token", default=None)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["tree", "table"]),
    default="tree",
    show_default=True,
)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def inspect(
    ctx,
    scope,
    dataset_version,
    profile,
    release,
    limit,
    depth,
    continuation_token,
    output_format,
    as_json,
):
    """List bounded S3 scopes; optionally check one embedding release's metadata."""
    try:
        if (
            as_json
            and ctx.get_parameter_source("output_format") != ParameterSource.DEFAULT
        ):
            raise ValueError("--json cannot be combined with an explicit --format")
        validate_selectors(
            scope, dataset_version, profile, release, limit, continuation_token
        )
        with redirect_stdout(sys.stderr):
            # Local override prevents the settings loader from creating an ID.
            settings = load_storage(destination="local")
            report = inspect_storage(
                settings,
                scope=scope,
                dataset_version=dataset_version,
                profile=profile,
                release=release,
                limit=limit,
                continuation_token=continuation_token,
            )
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        json.dumps(report)
        if as_json
        else render_report(report, format=output_format, depth=depth)
    )
    if report["state"] != "ok":
        ctx.exit(1)


if __name__ == "__main__":
    main()
