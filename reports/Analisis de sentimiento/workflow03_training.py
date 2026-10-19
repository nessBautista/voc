# /// script
# requires-python = ">=3.12,<3.13"
# ///
"""Educational sentiment training with independent ZenML and MLflow runs."""

# Marimo uses explicit returns to declare cell dependencies.
# ruff: noqa: PLR1711

import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium")


@app.cell
def imports():
    from pathlib import Path

    import marimo as mo
    import pandas as pd
    from voc_demo.sentiment.tracked_training import (
        class_distribution,
        prepare_dataset,
        run_training,
    )
    from voc_demo.settings import data_root, model_root

    return (
        Path,
        class_distribution,
        data_root,
        mo,
        model_root,
        pd,
        prepare_dataset,
        run_training,
    )


@app.cell(hide_code=True)
def introduction(mo):
    mo.md("""
    # Sentiment · training laboratory

    Load two CSVs, inspect their class proportions, then train each model independently.
    Both start from the original **RoBERTuito**.

    Required columns: `record_id,text,platform,label`.
    Labels: **negative, neutral, positive**. Stores can supply `rating` (1–5)
    instead of `label`: 1–2 negative, 3 neutral, 4–5 positive.
    Platforms: `app_store` / `google_play` for stores, `x` / `youtube` for social.

    [MLflow](http://127.0.0.1:5002) stores parameters, epoch metrics, evaluation and model files.
    [ZenML](http://127.0.0.1:8239) records each pipeline execution and its training step.
    CUDA is used when available.
    """)
    return


@app.cell
def dataset_inputs(data_root, mo):
    stores_file = mo.ui.file(filetypes=[".csv"], label="Upload stores CSV")
    social_file = mo.ui.file(filetypes=[".csv"], label="Upload social CSV")
    stores_path = mo.ui.text(
        value=str(data_root() / "sentiment/rated_reviews.csv"),
        label="Or stores CSV path",
    )
    social_path = mo.ui.text(
        value="/workspace/data/Social_reviews_manual_labeled.csv",
        label="Or social CSV path",
    )
    reload_data = mo.ui.run_button(label="Reload CSV paths")
    mo.vstack(
        [
            mo.md(
                "## 1. Load datasets\nAn uploaded CSV takes priority over its path. Clear the upload to use the path again."
            ),
            mo.hstack(
                [
                    mo.vstack([stores_file, stores_path]),
                    mo.vstack([social_file, social_path]),
                ]
            ),
            reload_data,
        ]
    )
    return reload_data, social_file, social_path, stores_file, stores_path


@app.cell
def split_controls(mo):
    train_percent = mo.ui.number(start=1, stop=98, value=60, step=1, label="Train %")
    val_percent = mo.ui.number(start=1, stop=98, value=20, step=1, label="Validation %")
    test_percent = mo.ui.number(start=1, stop=98, value=20, step=1, label="Test %")
    split_seed = mo.ui.number(
        start=0, stop=999999, value=42, step=1, label="Split seed"
    )
    mo.vstack(
        [
            mo.md(
                "## 2. Split and check proportions\nPercentages must sum to 100. Existing CSV partitions are recomputed. All reviews are retained; equivalent texts stay in one partition."
            ),
            mo.hstack([train_percent, val_percent, test_percent, split_seed]),
        ]
    )
    return split_seed, test_percent, train_percent, val_percent


@app.cell
def split_values(test_percent, train_percent, val_percent):
    percentages = [
        float(train_percent.value),
        float(val_percent.value),
        float(test_percent.value),
    ]
    return (percentages,)


@app.cell
def preview_helpers(Path, class_distribution, mo, pd, prepare_dataset):
    def load_and_preview(upload, path, domain, percentages, seed):
        try:
            content = (
                upload.value[0].contents
                if upload.value
                else Path(path).expanduser().read_bytes()
            )
            splits, source_hash = prepare_dataset(content, domain, percentages, seed)
        except (ValueError, OSError, pd.errors.ParserError) as error:
            mo.stop(True, mo.callout(f"{domain}: {error}", kind="warn"))
        distribution = class_distribution(splits)
        max_gap = distribution.difference_pp.abs().max()
        mo.output.replace(
            mo.vstack(
                [
                    mo.md(
                        f"### {domain.title()} · {sum(map(len, splits.values())):,} reviews"
                    ),
                    mo.ui.table(distribution.round(2), selection=None),
                    mo.callout(
                        f"Maximum deviation from the source class distribution: {max_gap:.2f} percentage points. "
                        "Small classes can differ because review counts are integers. Inspect the counts before training.",
                        kind="success" if max_gap <= 5 else "warn",
                    ),
                ]
            )
        )
        return splits, source_hash

    return (load_and_preview,)


@app.cell
def stores_preview(
    load_and_preview,
    percentages,
    reload_data,
    split_seed,
    stores_file,
    stores_path,
):
    _ = reload_data.value
    stores_splits, stores_hash = load_and_preview(
        stores_file, stores_path.value, "stores", percentages, int(split_seed.value)
    )
    return stores_hash, stores_splits


@app.cell
def social_preview(
    load_and_preview,
    percentages,
    reload_data,
    social_file,
    social_path,
    split_seed,
):
    _ = reload_data.value
    social_splits, social_hash = load_and_preview(
        social_file, social_path.value, "social", percentages, int(split_seed.value)
    )
    return social_hash, social_splits


@app.cell
def parameter_controls(mo):
    def controls():
        return mo.ui.dictionary(
            {
                "epochs": mo.ui.number(
                    start=1, stop=30, step=1, value=3, label="Epochs"
                ),
                "batch_size": mo.ui.number(
                    start=1, stop=64, step=1, value=8, label="Batch size"
                ),
                "learning_rate": mo.ui.number(
                    start=0.000001,
                    stop=0.001,
                    step=0.000001,
                    value=0.00002,
                    label="Learning rate",
                ),
                "seed": mo.ui.number(
                    start=0, stop=999999, step=1, value=42, label="Training seed"
                ),
            }
        )

    stores_parameters = controls()
    social_parameters = controls()
    train_stores = mo.ui.run_button(label="Train stores model")
    train_social = mo.ui.run_button(label="Train social model")
    mo.vstack(
        [
            mo.md(
                "## 3. Configure and train\nEpochs are passes over the training data. Batch size is the number of reviews per update. Learning rate controls the update size. The seed makes runs reproducible.\n\nThe best checkpoint is selected by validation macro F1. Test data is reserved for final evaluation and the original-model comparison. Model files are logged to the workspace's configured artifact storage."
            ),
            mo.hstack(
                [
                    mo.vstack([mo.md("### Stores"), stores_parameters, train_stores]),
                    mo.vstack([mo.md("### Social"), social_parameters, train_social]),
                ]
            ),
        ]
    )
    return social_parameters, stores_parameters, train_social, train_stores


@app.cell
def training_helper(data_root, mo, model_root, run_training):
    def execute(splits, source_hash, domain, percentages, parameters, split_seed):
        # Split seed is provenance; adapt() only receives training hyperparameters.
        with mo.status.spinner(
            title=f"Training {domain} · follow epoch metrics in MLflow…"
        ):
            return run_training(
                splits,
                source_hash,
                domain,
                percentages,
                parameters,
                data_root() / "sentiment/tracked-runs",
                model_root() / "sentiment",
                split_seed=split_seed,
            )

    return (execute,)


@app.cell
def stores_training(
    execute,
    mo,
    percentages,
    split_seed,
    stores_hash,
    stores_parameters,
    stores_splits,
    train_stores,
):
    mo.stop(
        not train_stores.value,
        mo.md("Stores training is waiting for **Train stores model**."),
    )
    stores_result = execute(
        stores_splits,
        stores_hash,
        "stores",
        percentages,
        stores_parameters.value,
        int(split_seed.value),
    )
    return (stores_result,)


@app.cell
def social_training(
    execute,
    mo,
    percentages,
    social_hash,
    social_parameters,
    social_splits,
    split_seed,
    train_social,
):
    mo.stop(
        not train_social.value,
        mo.md("Social training is waiting for **Train social model**."),
    )
    social_result = execute(
        social_splits,
        social_hash,
        "social",
        percentages,
        social_parameters.value,
        int(split_seed.value),
    )
    return (social_result,)


@app.cell
def result_helper(mo, pd):
    def show_result(result):
        metrics = result["metrics"]
        mo.output.replace(
            mo.vstack(
                [
                    mo.md(
                        f"### {result['domain'].title()} · completed\nBest epoch: **{metrics['best_epoch']}** · device: `{metrics['device']}`\n\nMLflow run: `{result['mlflow_run_id']}`\n\nZenML run: `{result['zenml_run_id']}`\n\nLocal model: `{result['output']}/model`"
                    ),
                    mo.ui.table(
                        pd.DataFrame(
                            [
                                {
                                    "model": "Original",
                                    "test_macro_f1": metrics["baseline_test_macro_f1"],
                                },
                                {
                                    "model": "Adapted",
                                    "test_macro_f1": metrics["test"]["macro avg"][
                                        "f1-score"
                                    ],
                                },
                            ]
                        ).round(4)
                    ),
                    mo.ui.table(
                        pd.DataFrame(metrics["history"]).round(4), label="Epoch history"
                    ),
                    mo.ui.table(
                        pd.DataFrame(
                            {
                                label: metrics["test"][label]
                                for label in metrics["label_order"]
                            }
                        ).T.round(4),
                        label="Test metrics by class",
                    ),
                    mo.ui.table(
                        pd.DataFrame(
                            metrics["confusion_matrix"],
                            index=metrics["label_order"],
                            columns=metrics["label_order"],
                        ),
                        label="Confusion matrix: true rows, predicted columns",
                    ),
                ]
            )
        )

    return (show_result,)


@app.cell
def stores_evaluation(show_result, stores_result):
    show_result(stores_result)
    return


@app.cell
def social_evaluation(show_result, social_result):
    show_result(social_result)
    return


if __name__ == "__main__":
    app.run()
