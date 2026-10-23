"""Operator commands for discovery and independent sentiment preparation."""

import json
import sys
from contextlib import redirect_stdout

import click

from . import producer


class WorkflowCommands(click.Group):
    def parse_args(self, ctx, args):
        ctx.meta["workflow_args"] = list(args)
        return super().parse_args(ctx, args)

    def invoke(self, ctx):
        args = ctx.meta["workflow_args"]
        try:
            options = [a.split("=", 1)[0] for a in args if a.startswith("--")]
            if len(options) != len(set(options)):
                raise click.UsageError("Specify each workflow option only once")
            return super().invoke(ctx)
        except click.ClickException as error:
            if "--json" not in args:
                raise
            click.echo(json.dumps({"state": "error", "error": error.format_message()}))
            click.echo(error.format_message(), err=True)
            raise SystemExit(error.exit_code) from error


@click.group(cls=WorkflowCommands)
def analysis():
    """Run discovery and inspect local producer state. Query results with voc analysis."""


@click.group(cls=WorkflowCommands)
def sentiment():
    """Prepare cached review sentiment independently of topics or embeddings."""


def common_options(function):
    for decorator in reversed(
        [
            click.option("--dataset-version", default="latest", show_default=True),
            click.option("--source", type=click.Choice(["s3"]), default="s3"),
            click.option(
                "--limit",
                type=click.IntRange(min=1),
                default=None,
                help="Select first N reviews. Overrides analysis TOML selection.",
            ),
            click.option(
                "--sentiment-config",
                default="config/analysis/sentiment.toml",
                type=click.Path(exists=True, dir_okay=False),
            ),
            click.option(
                "--local-files-only/--allow-model-download",
                default=True,
                help="Use cached pinned RoBERTuito weights by default.",
            ),
            click.option(
                "--json",
                "as_json",
                is_flag=True,
                help="One JSON result on stdout, logs on stderr.",
            ),
        ]
    ):
        function = decorator(function)
    return function


def launch(kind, config, sentiment_config, as_json, **kwargs):
    from voc_ml.sentiment import load_config as load_sentiment

    from .config import load_config

    try:
        with redirect_stdout(sys.stderr):
            state = producer.create_run(
                kind=kind,
                config=load_config(config) if config else None,
                sentiment_config=load_sentiment(sentiment_config),
                **kwargs,
            )
    except (ValueError, TypeError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo("Workflow producer: " + state["run_id"], err=True)
    with redirect_stdout(sys.stderr):
        state = producer.execute_run(state["run_id"])
    emit(state, as_json)


def emit(state, as_json):
    click.echo(json.dumps(state, indent=None if as_json else 2))
    if state["state"] != "completed":
        click.echo(
            state.get("error", {}).get("message", "Workflow is incomplete"), err=True
        )
        raise SystemExit(1)


@analysis.command("run")
@click.option("--embedding-version", default="latest", show_default=True)
@click.option("--profile", default="minilm-verbatim-v1", show_default=True)
@click.option(
    "--config",
    default="config/analysis/bertopic.toml",
    type=click.Path(exists=True, dir_okay=False),
)
@common_options
def analysis_run(**kwargs):
    """Discover topics and sentiment, then save a validated local DuckDB candidate."""
    launch("analysis", **kwargs)


@sentiment.command("workflow")
@common_options
def sentiment_workflow(**kwargs):
    """Classify prepared dataset reviews and populate the shared prediction cache."""
    launch("sentiment", config=None, **kwargs)


def inspect(kind, run_id, as_json):
    try:
        with redirect_stdout(sys.stderr):
            state = producer.inspect_run(run_id)
        if state["kind"] != kind:
            raise ValueError("Run belongs to the other workflow kind")
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise click.ClickException(str(error)) from error
    emit(state, as_json)


@analysis.command("inspect")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True)
def analysis_inspect(run_id, as_json):
    """Inspect completion or failure without contacting ZenML or MLflow."""
    inspect("analysis", run_id, as_json)


@sentiment.command("inspect")
@click.argument("run_id")
@click.option("--json", "as_json", is_flag=True)
def sentiment_inspect(run_id, as_json):
    inspect("sentiment", run_id, as_json)


@analysis.command("represent")
@click.argument("analysis_id")
@click.option(
    "--config",
    default="config/analysis/representation.toml",
    type=click.Path(exists=True, dir_okay=False),
    show_default=True,
)
@click.option(
    "--local-files-only/--allow-model-download",
    default=True,
    help="Use cached pinned embedding weights for KeyBERT/MMR.",
)
@click.option("--json", "as_json", is_flag=True)
def analysis_represent(analysis_id, config, local_files_only, as_json):
    """Refine keywords into a child candidate. Keep groups, titles and sentiment."""
    from .representation import execute_representation

    try:
        with redirect_stdout(sys.stderr):
            state = execute_representation(
                analysis_id, config, local_files_only=local_files_only
            )
    except (ValueError, TypeError, OSError, LookupError) as error:
        raise click.ClickException(str(error)) from error
    emit(state, as_json)


@analysis.command("interpret")
@click.argument("analysis_id")
@click.option(
    "--topics", required=True, help="Comma-separated topic IDs, excluding -1."
)
@click.option(
    "--config",
    default="config/analysis/interpretation.toml",
    type=click.Path(exists=True, dir_okay=False),
    show_default=True,
)
@click.option("--dry-run", is_flag=True, help="Plan only. This is the default.")
@click.option(
    "--live",
    is_flag=True,
    help="Execute, sending only requests without reusable responses.",
)
@click.option(
    "--refresh",
    is_flag=True,
    help="Bypass saved responses. Requires --live to send requests.",
)
@click.option("--json", "as_json", is_flag=True)
def analysis_interpret(analysis_id, topics, config, dry_run, live, refresh, as_json):
    """Interpret selected topics into a local child, with saved-response reuse."""
    from .interpretation import interpret

    try:
        if dry_run and live:
            raise ValueError("Choose --dry-run or --live, not both")
        ids = [int(t.strip()) for t in topics.split(",")]
        with redirect_stdout(sys.stderr):
            state = interpret(analysis_id, ids, config, live=live, refresh=refresh)
    except (ValueError, TypeError, OSError, LookupError) as error:
        raise click.ClickException(str(error)) from error
    if state["state"] == "dry_run":
        click.echo(json.dumps(state, indent=None if as_json else 2))
    else:
        emit(state, as_json)
