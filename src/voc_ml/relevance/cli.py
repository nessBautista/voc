"""Thin commands around annotation recovery, fitting and local filter decisions."""

import json
from pathlib import Path

import click


@click.group()
def relevance():
    """Reconstruct and apply the social relevance filter using verified local vectors."""


def local_option(function):
    return click.option(
        "--local-inputs", required=True, type=click.Path(exists=True, dir_okay=False)
    )(function)


@relevance.command("prepare-annotations")
@local_option
@click.option("--notebook", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--output", required=True, type=click.Path())
def prepare_annotations(local_inputs, notebook, output):
    from .preparation import prepare_annotations as prepare

    try:
        result = prepare(local_inputs, notebook, output)
    except (ValueError, OSError, KeyError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


@relevance.command("train")
@local_option
@click.option(
    "--annotations", required=True, type=click.Path(exists=True, dir_okay=False)
)
@click.option("--output", required=True, type=click.Path())
@click.option(
    "--classifier",
    type=click.Choice(["svm", "logreg"]),
    default="svm",
    show_default=True,
)
@click.option("--reference-validation", type=click.Path(exists=True, dir_okay=False))
def train_filter(local_inputs, annotations, output, classifier, reference_validation):
    from .training import train

    click.echo(
        f"Verifying annotations/folds; fitting five validation folds and final {classifier}.",
        err=True,
    )
    try:
        result = train(
            local_inputs,
            annotations,
            output,
            classifier=classifier,
            reference_validation=reference_validation,
        )
    except (ValueError, OSError, KeyError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


@relevance.command("apply")
@local_option
@click.option("--model", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--model-sha256", required=True)
@click.option("--output", required=True, type=click.Path())
def apply_filter(local_inputs, model, model_sha256, output):
    from voc.analysis.local_inputs import load_local_config, read_local_inputs
    from voc.datasets.manifest import file_hash
    from voc.relevance import decisions, load_model

    try:
        snapshot, bundle = read_local_inputs(load_local_config(local_inputs))
        fitted = load_model(
            model, sha256=model_sha256, profile_id=bundle.info["profile"]["profile_id"]
        )
        reviews = bundle.reviews[["record_id"]].merge(
            snapshot.data, on="record_id", validate="one_to_one", sort=False
        )
        result = decisions(reviews, bundle.vectors, fitted)
        target = Path(output)
        target.mkdir(parents=True, exist_ok=False)
        result.to_csv(target / "decisions.csv", index=False)
        manifest = {
            "schema_version": "voc-relevance-decisions-v1",
            "model_sha256": model_sha256,
            "dataset_release_id": snapshot.info["release_id"],
            "embedding_release_id": bundle.info["embedding_release_id"],
            "decisions_sha256": file_hash(target / "decisions.csv"),
            "rows": len(result),
            "kept": int(result.keep.sum()),
            "excluded": int((~result.keep).sum()),
            "training_rows_in_input": int(
                reviews.record_id.isin(fitted["training_record_ids"]).sum()
            ),
            "is_quality_evaluation": False,
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    except (ValueError, OSError, KeyError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(manifest, indent=2))


@relevance.command("import-review")
@click.option(
    "--annotations", required=True, type=click.Path(exists=True, dir_okay=False)
)
@click.option(
    "--reviewed-csv", required=True, type=click.Path(exists=True, dir_okay=False)
)
@click.option("--output", required=True, type=click.Path())
def import_review(annotations, reviewed_csv, output):
    from .preparation import import_review as merge

    try:
        result = merge(annotations, reviewed_csv, output)
    except (ValueError, OSError, KeyError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))
