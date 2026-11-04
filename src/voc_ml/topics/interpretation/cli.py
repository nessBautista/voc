"""Fixed-prompt production CLI and read-only historical experiment inspection."""

import json

import click


@click.group()
def interpretation():
    """Interpret G3/R2 topics with the fixed voc-dev prompt and SDK mechanism."""


def _store(name="prototype-execution"):
    from voc.paths import data_root

    from .ledger import InterpretationStore

    return InterpretationStore(data_root() / "topics/interpretation" / name)


def _call(operation):
    try:
        result = operation()
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


def _prepare(topic_ids, inputs_path, config_path):
    from .preparation import load_inputs
    from .prototype import load_configuration, prepare, provider_policy

    return prepare(
        load_inputs(inputs_path),
        topic_ids,
        load_configuration(config_path),
        provider_policy("config/topics/interpretation-provider.toml"),
    )


def _options(command):
    command = click.option(
        "--topic-id", "topic_ids", multiple=True, required=True, type=int
    )(command)
    command = click.option(
        "--inputs",
        "inputs_path",
        default="config/topics/interpretation-inputs.toml",
        show_default=True,
        type=click.Path(exists=True, dir_okay=False),
    )(command)
    return click.option(
        "--config",
        "config_path",
        default="config/topics/interpretation.toml",
        show_default=True,
        type=click.Path(exists=True, dir_okay=False),
    )(command)


@interpretation.command("plan")
@_options
def plan(topic_ids, inputs_path, config_path):
    """Verify inputs and summarize explicit topics. No writes or provider access."""
    from .prototype import summary

    _call(lambda: summary(_prepare(topic_ids, inputs_path, config_path)))


@interpretation.command("create")
@_options
def create(topic_ids, inputs_path, config_path):
    """Freeze one separate fixed-prompt execution. No model request or overwrite."""
    from .prototype import create as create_execution
    from .provider import check_catalog

    _call(
        lambda: create_execution(
            _prepare(topic_ids, inputs_path, config_path),
            store=_store(),
            catalog_reader=check_catalog,
        )
    )


@interpretation.command("inspect")
def inspect():
    """Verify fixed-prompt receipts and show pending topics without network."""
    from .prototype import inspect as inspect_execution

    _call(lambda: inspect_execution(_store()))


@interpretation.command("run")
@click.argument("topic_id", type=click.IntRange(min=0))
def run(topic_id):
    """Run one frozen topic via ZenML/MLflow. No automatic retries."""
    from .prototype import inspect as inspect_execution
    from .tracking import execute_workflow

    try:
        inspect_execution(_store())
        result = execute_workflow(str(topic_id), store=_store())
    except Exception:  # noqa: BLE001 - never expose provider or tracking secrets
        raise click.ClickException(
            "Interpretation did not complete. Run interpretation inspect before continuing."
        ) from None
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))


@interpretation.command("review-failure")
@click.argument("topic_id", type=click.IntRange(min=0))
def review_failure(topic_id):
    """Retain a known length failure, without resending it."""
    from .production import review_length_failure
    from .prototype import inspect as inspect_execution

    def review():
        inspect_execution(_store())
        return review_length_failure(str(topic_id), store=_store())

    _call(review)


@interpretation.command("history")
@click.argument("phase", type=click.Choice(["development", "reserve"]))
def history(phase):
    """Read previous experiment receipts. Does not enable or replay experiments."""
    from .production import inspect_study

    name = "execution" if phase == "development" else "reserve-execution"
    _call(lambda: inspect_study(_store(name)))


@interpretation.command("review")
@click.option(
    "--plan",
    "plan_path",
    default="data/topics/interpretation/plan.json",
    type=click.Path(exists=True, dir_okay=False),
)
def review(plan_path):
    """Read historical development cases only. Does not open a new experiment."""
    from .files import read_draft

    def read():
        draft = read_draft(plan_path)
        return {
            "plan_sha256": draft["plan_sha256"],
            "partition": "development",
            "cases": draft["plan"]["cases"]["development"],
            "model_requests": 0,
        }

    _call(read)
