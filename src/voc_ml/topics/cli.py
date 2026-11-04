"""Commands for bounded topic fitting with explicit input identities."""

import json
import sys
from contextlib import redirect_stdout

import click

from .interpretation.cli import interpretation


@click.group()
def topics():
    """Fit and inspect bounded local topic analyses."""


@topics.command("workflow")
@click.option("--dataset-version", required=True)
@click.option(
    "--embeddings", required=True, type=click.Path(exists=True, file_okay=False)
)
@click.option("--manifest-sha256", required=True)
@click.option(
    "--config",
    default="config/topics/base.toml",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False),
)
def workflow(dataset_version, embeddings, manifest_sha256, config):
    """Run a configured grouping through local ZenML/MLflow. Never chooses latest."""
    from .config import load_config
    from .production import create_run
    from .store import local_store
    from .workflow import execute_workflow

    store = local_store()
    run_id = None
    try:
        with redirect_stdout(sys.stderr):
            state = create_run(
                dataset_version,
                embeddings,
                manifest_sha256,
                load_config(config),
                store=store,
            )
            run_id = state["run_id"]
            click.echo(f"Topic producer: {run_id}", err=True)
            result = execute_workflow(run_id, store=store)
    except Exception as error:
        raise click.ClickException(f"{error}; producer={run_id}") from error
    click.echo(json.dumps(result, indent=2))


@topics.command("inspect")
@click.argument("run_id")
def inspect(run_id):
    """Read local producer state and revalidate its completed artifact."""
    from .production import inspect_run

    try:
        result = inspect_run(run_id)
    except (ValueError, KeyError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


@topics.group("study")
def study():
    """Create, inspect and run a bounded G/R protocol one attempt at a time."""


def _study_call(operation, *args, **kwargs):
    from .store import local_store

    try:
        with redirect_stdout(sys.stderr):
            result = operation(*args, store=local_store(), **kwargs)
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


@study.command("create")
@click.argument("parent_run_id")
@click.option("--manifest-sha256", required=True)
def study_create(parent_run_id, manifest_sha256):
    """Freeze a study from an existing verified G0, without fitting."""
    from .studies import create_study

    _study_call(create_study, parent_run_id, manifest_sha256)


@study.command("inspect")
@click.argument("study_id")
def study_inspect(study_id):
    """Verify saved results and build comparisons and review templates."""
    from .studies import inspect_study

    _study_call(inspect_study, study_id)


@study.command("run")
@click.argument("study_id")
@click.argument("variant", type=click.Choice(["G0", "G1", "G2", "G3", "R1", "R2"]))
@click.option("--seed", default=42, type=int, show_default=True)
def study_run(study_id, variant, seed):
    """Start exactly one tracked attempt. Failed attempts cannot be replayed."""
    from .studies import run_variant

    _study_call(run_variant, study_id, variant, seed=seed)


@study.command("preselect")
@click.argument("study_id")
@click.option(
    "--variant",
    "variants",
    multiple=True,
    required=True,
    type=click.Choice(["G0", "G1", "G2", "G3"]),
)
@click.option("--reviewer", required=True)
@click.option("--rationale", required=True)
def study_preselect(study_id, variants, reviewer, rationale):
    """Freeze up to two configurations before running sensitivity seeds."""
    from .studies import select_candidates

    _study_call(select_candidates, study_id, variants, reviewer, rationale)


@study.command("select-parent")
@click.argument("study_id")
@click.argument("variant", type=click.Choice(["G0", "G1", "G2", "G3"]))
@click.option("--reviewer", required=True)
@click.option("--rationale", required=True)
def study_parent(study_id, variant, reviewer, rationale):
    """Freeze one seed-42 grouping for R after reviewing sensitivity."""
    from .studies import select_representation_parent

    _study_call(select_representation_parent, study_id, variant, reviewer, rationale)


@topics.command("preflight")
@click.option("--dataset-version", required=True)
@click.option(
    "--embeddings", required=True, type=click.Path(exists=True, file_okay=False)
)
@click.option("--manifest-sha256", required=True)
@click.option(
    "--scope", type=click.Choice(["pilot", "full"]), default="pilot", show_default=True
)
def preflight_command(dataset_version, embeddings, manifest_sha256, scope):
    """Verify a 5,000-row pilot or full input, without fitting or creating runs."""
    from voc.topics.scope import TopicSelection

    from .preflight import preflight

    # Resolve an explicit scope before reading any input.
    selection = (
        TopicSelection() if scope == "pilot" else TopicSelection("full", None, None)
    )
    try:
        with redirect_stdout(sys.stderr):
            result = preflight(dataset_version, embeddings, manifest_sha256, selection)
    except (ValueError, LookupError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


@topics.command("resources")
def resources_command():
    """Observe memory, CPU and disk for the pilot. Does not reserve resources."""
    from voc.paths import data_root

    from .resources import resource_preflight

    try:
        result = resource_preflight(data_root())
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


@topics.group("corpus")
def corpus_command():
    """Freeze and execute a corpus pilot or an explicit full-population study."""


@corpus_command.command("create")
@click.option("--dataset-version", required=True)
@click.option(
    "--embeddings", required=True, type=click.Path(exists=True, file_okay=False)
)
@click.option("--manifest-sha256", required=True)
def corpus_create(dataset_version, embeddings, manifest_sha256):
    """Freeze inputs and budget without fitting models."""
    from .corpus import create_pilot

    _study_call(create_pilot, dataset_version, embeddings, manifest_sha256)


@corpus_command.command("run")
@click.argument("variant", type=click.Choice(["G0", "G3", "R2"]))
def corpus_run(variant):
    """Execute one supervised pilot attempt. No retries or full-corpus fitting."""
    from .corpus import run_pilot

    _study_call(run_pilot, variant)


@corpus_command.command("inspect")
def corpus_inspect():
    """Verify and summarize the saved pilot results."""
    from .corpus import inspect_pilot

    _study_call(inspect_pilot)


@corpus_command.group("full")
def full_command():
    """Explicit full-population study, independent of the completed pilot."""


@full_command.command("resources")
def full_resources_command():
    """Observe the full budget without starting models or reserving memory."""
    from .full_corpus import full_resources

    _study_call(full_resources)


@full_command.command("create")
def full_create_command():
    """Verify the completed pilot and freeze a new full-population protocol."""
    from .full_corpus import create_full

    _study_call(create_full)


@full_command.command("run")
@click.argument("variant", type=click.Choice(["G0", "G3", "R2"]))
def full_run_command(variant):
    """Execute exactly one full-population attempt, with no automatic retries."""
    from .full_corpus import run_full

    _study_call(run_full, variant)


@full_command.command("inspect")
def full_inspect_command():
    """Verify saved full-population results without fitting models."""
    from .full_corpus import inspect_full

    _study_call(inspect_full)


# Interpretation preparation is independent of the fitting and tracking paths.
topics.add_command(interpretation)
