"""Click commands for the ML project; deliberately separate from the VOC CLI."""

import json
import os
import tomllib
import uuid
from pathlib import Path

import click


def environment():
    from voc.paths import data_root

    os.environ.setdefault("VOC_DATA_DIR", str(data_root()))
    os.environ.setdefault("ZENML_CONFIG_PATH", str(data_root() / "zenml-client"))
    os.environ.setdefault(
        "ZENML_CUSTOM_SOURCE_ROOT", str(Path(__file__).resolve().parents[2])
    )
    os.environ.setdefault("ZENML_ANALYTICS_OPT_IN", "false")


@click.group()
def main():
    """ML environment, collection/preparation pipelines and publication."""
    environment()


@main.command()
def setup():
    """Register and select the configured ZenML stack."""
    from voc_dev.bootstrap import bootstrap

    click.echo(bootstrap())


@main.command("storage-info")
@click.option("--config", default=None, type=click.Path(exists=True, dir_okay=False))
@click.option("--destination", type=click.Choice(["local", "s3"]), default=None)
def storage_info(config, destination):
    """Inspect intended storage settings; does not reconfigure ZenML or MLflow."""
    from voc.storage import load_storage

    try:
        settings = load_storage(config, destination=destination)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(settings.describe(), indent=2))


@main.group()
def dataset():
    """Manage the ML dataset workflow."""


@dataset.command("init")
@click.option(
    "--seed", type=click.Path(exists=True), envvar="VOC_SEED_PATH", required=True
)
@click.option("--config", default="config/collector.toml")
def initialize(seed, config):
    from voc.collector import initialize_raw_store, load_config

    cfg = load_config(config)
    click.echo(initialize_raw_store(cfg["raw_store"], seed_path=seed))


@dataset.command("run")
@click.option("--config", default="config/collector.toml")
@click.option("--rules", default="config/preparation.toml")
@click.option(
    "--refresh/--no-refresh",
    default=False,
    help="Refresh requires --live acknowledgement.",
)
@click.option("--live", is_flag=True, help="Allow real store requests.")
@click.option("--operation-id", default=None)
def run(config, rules, refresh, live, operation_id):
    if refresh and not live:
        raise click.UsageError("Use --refresh --live to make store requests")
    from voc.collector import load_config

    from .identity import code_identity
    from .pipelines.dataset import dataset_pipeline

    cfg = load_config(config)
    with open(rules, "rb") as f:
        settings = tomllib.load(f)
    result = dataset_pipeline(
        config=cfg,
        rules=settings,
        refresh=refresh,
        operation_id=operation_id or str(uuid.uuid4()),
        project_identity=code_identity(),
    )
    if result is None:
        raise click.ClickException("No completed run returned")
    from .publication import publish_run

    click.echo(json.dumps(publish_run(str(result.id)), indent=2))


@dataset.command("publish")
@click.argument("run_id")
def publish(run_id):
    """Retry publication of an already-completed dataset run."""
    from .publication import publish_run

    click.echo(json.dumps(publish_run(run_id), indent=2))


@dataset.command("share")
@click.argument("run_id")
def share(run_id):
    """Upload a published run's raw/workable release and advance shared latest."""
    from botocore.exceptions import BotoCoreError, ClientError

    from .publication import share_run

    try:
        result = share_run(run_id)
    except (
        ValueError,
        LookupError,
        RuntimeError,
        OSError,
        BotoCoreError,
        ClientError,
    ) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


class EmbeddingCommands(click.Group):
    """Return JSON for argument errors too, before allocating a producer run."""

    def parse_args(self, ctx, args):
        ctx.meta["embedding_args"] = list(args)
        return super().parse_args(ctx, args)

    def invoke(self, ctx):
        import sys

        args = ctx.meta["embedding_args"]
        wants_json = "--json" in args
        try:
            for option in (
                "--dataset-version",
                "--source",
                "--profile",
                "--limit",
                "--batch-size",
            ):
                if sum(arg.split("=", 1)[0] == option for arg in args) > 1:
                    raise click.UsageError(f"Specify {option} only once")
            return super().invoke(ctx)
        except click.ClickException as error:
            if not wants_json:
                raise
            click.echo(json.dumps({"state": "error", "error": error.format_message()}))
            click.echo(error.format_message(), err=True)
            sys.exit(error.exit_code)


@main.group(cls=EmbeddingCommands)
def embeddings():
    """Produce and inspect local embedding runs."""


@embeddings.command("workflow")
@click.option("--dataset-version", default="latest", show_default=True)
@click.option("--source", type=click.Choice(["s3"]), default="s3")
@click.option("--profile", default="minilm-verbatim-v1", show_default=True)
@click.option("--limit", type=click.IntRange(min=1), default=None)
@click.option("--batch-size", type=click.IntRange(min=1), default=32, show_default=True)
@click.option(
    "--local-files-only", is_flag=True, help="Use an already downloaded encoder model."
)
@click.option(
    "--json", "as_json", is_flag=True, help="One JSON result on stdout; logs on stderr."
)
def embeddings_workflow(
    dataset_version, source, profile, limit, batch_size, local_files_only, as_json
):
    import sys
    from contextlib import redirect_stdout

    from .embeddings.producer import create_run, execute_run

    try:
        state = create_run(
            dataset_version=dataset_version,
            source=source,
            profile=profile,
            limit=limit,
            batch_size=batch_size,
            local_files_only=local_files_only,
        )
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo("Embedding producer: " + state["producer_run_id"], err=True)
    # Import/run orchestration inside redirection: ZenML initializes its loggers here.
    with redirect_stdout(sys.stderr):
        state = execute_run(state["producer_run_id"])
    click.echo(json.dumps(state, indent=None if as_json else 2))
    if state["state"] != "completed":
        click.echo(
            state.get("error", {}).get("message", "Producer did not complete"), err=True
        )
        sys.exit(1)


@embeddings.command("inspect")
@click.argument("producer_run_id")
@click.option("--json", "as_json", is_flag=True)
def embeddings_inspect(producer_run_id, as_json):
    import sys

    from .embeddings.producer import inspect_run

    try:
        state = inspect_run(producer_run_id)
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(state, indent=None if as_json else 2))
    if state["state"] != "completed":
        sys.exit(1)


@embeddings.command("export")
@click.argument("producer_run_id")
@click.option("--json", "as_json", is_flag=True)
def embeddings_export(producer_run_id, as_json):
    """Prepare a portable local release from a completed run; no publication."""
    import sys
    from contextlib import redirect_stdout

    from .embeddings.export import export_run

    try:
        with redirect_stdout(sys.stderr):
            reference = export_run(producer_run_id)
    except Exception as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(reference, indent=None if as_json else 2))


if __name__ == "__main__":
    main()
