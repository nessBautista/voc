"""Thin analysis commands. Shared queries own all filtering and calculations."""

import json
import sqlite3
import sys
from contextlib import redirect_stdout

import click

from . import schema


def _storage_errors():
    # DuckDB arrives with the publications extra; without it, commands explain.
    try:
        import duckdb
    except ImportError:
        return (OSError, sqlite3.Error)
    return (OSError, sqlite3.Error, duckdb.Error)


class AnalysisCommands(click.Group):
    """Keep JSON errors machine-readable, including Click parsing failures."""

    def parse_args(self, ctx, args):
        ctx.meta["analysis_json"] = "--json" in args
        try:
            return super().parse_args(ctx, args)
        except click.ClickException as error:
            self.fail(ctx, error, "invalid_request", error.exit_code)

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except click.ClickException as error:
            self.fail(ctx, error, "invalid_request", error.exit_code)
        except LookupError as error:
            self.fail(ctx, error, "not_found")
        except ImportError as error:
            self.fail(ctx, error, "missing_dependency")
        except (ValueError, TypeError) as error:
            self.fail(ctx, error, "invalid_request_or_analysis")
        except _storage_errors() as error:
            self.fail(ctx, error, "storage_error")

    @staticmethod
    def fail(ctx, error, code, exit_code=1):
        message = (
            error.format_message()
            if isinstance(error, click.ClickException)
            else (
                "Install voc[publications] to read analyses"
                if isinstance(error, ImportError)
                else str(error)
            )
        )
        if not ctx.meta.get("analysis_json"):
            raise click.ClickException(message) from error
        # No analysis ID is reported as resolved when a command fails.
        payload = schema.envelope(
            ctx.meta.get("analysis_kind", ctx.invoked_subcommand),
            None,
            state="error",
            error={"code": code, "message": message},
        )
        click.echo(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        click.echo(message, err=True)
        ctx.exit(exit_code)


@click.group("analysis", cls=AnalysisCommands)
def analysis_cli():
    """Inspect and query saved local analyses without running models."""


def _queries():
    from . import queries

    return queries


def _output(kind, function, as_json, *args, **kwargs):
    click.get_current_context().meta["analysis_kind"] = kind
    with redirect_stdout(sys.stderr):
        result = function(*args, **kwargs)
    click.echo(
        json.dumps(
            result, ensure_ascii=False, allow_nan=False, indent=None if as_json else 2
        )
    )


@analysis_cli.command("list")
@click.option(
    "--json", "as_json", is_flag=True, help="Emit a machine-readable JSON envelope."
)
def list_command(as_json):
    """List completed, checksum-verified local candidates."""
    _output("list", _queries().list_candidates, as_json)


@analysis_cli.command("inspect")
@click.argument("analysis_id")
@click.option("--json", "as_json", is_flag=True)
def inspect_command(analysis_id, as_json):
    """Verify artifact checksums and show the analysis manifest."""
    _output("inspect", _queries().inspect_analysis, as_json, analysis_id)


@analysis_cli.command("query")
@click.argument("analysis_id")
@click.option(
    "--kind", type=click.Choice(schema.KINDS), default="overview", show_default=True
)
@click.option("--platform", help="Exact source name, such as google_play.")
@click.option(
    "--search",
    help="Literal review-text substring, case-insensitive and accent-sensitive (1–500 characters).",
)
@click.option("--assessment", type=click.Choice(schema.ASSESSMENTS))
@click.option(
    "--evidence",
    type=click.Choice(schema.EVIDENCE),
    help="Filter to saved representative or model-cited reviews.",
)
@click.option(
    "--sort",
    type=click.Choice(schema.REVIEW_SORTS),
    default="source",
    help="Reviews only.",
)
@click.option("--sentiment", type=click.Choice(schema.SENTIMENT_LABELS))
@click.option("--status", type=click.Choice(schema.SENTIMENT_STATUSES))
@click.option(
    "--topic-id",
    type=click.IntRange(-1, 2**31 - 1),
    help="Analysis-local topic ID. Use -1 for outliers.",
)
@click.option(
    "--start", help="Inclusive UTC date, YYYY-MM-DD. Missing dates are excluded."
)
@click.option("--end", help="Inclusive UTC date, YYYY-MM-DD.")
@click.option(
    "--limit",
    type=click.IntRange(1, 1000),
    default=50,
    show_default=True,
    help="Page size for reviews.",
)
@click.option(
    "--offset", type=click.IntRange(0, 2**63 - 1), default=0, show_default=True
)
@click.option("--json", "as_json", is_flag=True)
def query_command(analysis_id, kind, limit, offset, sort, as_json, **filters):
    """Query saved results. Filters combine with AND; proportions use model labels.

    Monthly results include a null bucket for missing/invalid dates.
    """
    _output(
        kind,
        _queries().query,
        as_json,
        analysis_id,
        kind,
        filters=filters,
        sort=sort,
        limit=limit,
        offset=offset,
    )
