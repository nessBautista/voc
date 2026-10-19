# /// script
# requires-python = ">=3.12,<3.13"
# ///
"""Educational transfer learning: frozen layers, language audits and evaluation."""

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
    from voc_demo.sentiment.tracked_training import class_distribution, prepare_dataset
    from voc_demo.sentiment.transfer_training import run_training
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
    # Sentiment · transfer-learning laboratory

    Load two CSVs, inspect their class proportions, then train each model independently.
    Both start from the original **RoBERTuito**. First review languages and class
    proportions, then choose how much of the pretrained model to update.

    **Head only** freezes the encoder and trains the classifier (the default).
    **Last encoder blocks + head** also trains the final N transformer blocks.
    **Full fine-tuning** updates every parameter for comparison.

    Required columns: `record_id,text,platform,label`.
    Labels: **negative, neutral, positive**. Stores can supply `rating` (1–5)
    instead of `label`: 1–2 negative, 3 neutral, 4–5 positive.
    Platforms: `app_store` / `google_play` for stores, `x` / `youtube` for social.

    [MLflow](http://127.0.0.1:5002) stores parameters, epoch metrics, evaluation and model files.
    [ZenML](http://127.0.0.1:8239) records each pipeline execution and its training step.
    CUDA is used when available.
    Macro F1 treats the three classes equally; accuracy is the share of correct
    predictions and can hide poor minority-class performance. Weighted F1 weights classes by their support.
    Compare training settings using validation; reserve test data for the final report. This is supervised,
    whole-review sentiment classification.
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
def language_controls(mo):
    audit_full = mo.ui.checkbox(value=False, label="Audit every review (slower)")
    spanish_only = mo.ui.checkbox(
        value=False,
        label="Keep only confidently detected Spanish (requires full audit)",
    )
    language_threshold = mo.ui.number(
        start=0.5, stop=1.0, step=0.05, value=0.8, label="Minimum detector score"
    )
    min_letters = mo.ui.number(
        start=5, stop=100, step=1, value=20, label="Minimum letters to detect language"
    )
    audit_stores = mo.ui.run_button(label="Review stores dataset")
    audit_social = mo.ui.run_button(label="Review social dataset")
    mo.vstack(
        [
            mo.md(
                "## 2. Review languages and partitions\nBy default, audit a reproducible sample of up to 500 reviews per domain and retain all input rows. Detector scores are heuristic; short and mixed-language text needs human review. Filtering is optional and requires auditing every row. Review flagged texts before deciding to exclude them. Full audits of large datasets can take several minutes."
            ),
            audit_full,
            spanish_only,
            mo.hstack([language_threshold, min_letters]),
            mo.hstack([audit_stores, audit_social]),
        ]
    )
    return (
        audit_full,
        audit_social,
        audit_stores,
        language_threshold,
        min_letters,
        spanish_only,
    )


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
                "### Split proportions\nPercentages must sum to 100. Existing CSV partitions are recomputed. All reviews are retained; equivalent texts stay in one partition."
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
    from voc_demo.sentiment.language_audit import audit_languages, select_language_rows

    def load_and_preview(
        upload,
        path,
        domain,
        percentages,
        seed,
        audit_now,
        full,
        threshold,
        letters,
        spanish_only,
    ):
        mo.stop(
            not audit_now,
            mo.md(
                f"Press **Review {domain} dataset** to inspect languages and prepare partitions."
            ),
        )
        try:
            content = (
                upload.value[0].contents
                if upload.value
                else Path(path).expanduser().read_bytes()
            )
            with mo.status.spinner(title=f"Reviewing {domain} languages…"):
                frame, audit, metadata = audit_languages(
                    content, full=full, threshold=threshold, min_letters=letters
                )
            selected, language_info = select_language_rows(
                frame, audit, metadata, spanish_only
            )
            splits, source_hash = prepare_dataset(selected, domain, percentages, seed)
        except (ValueError, OSError, pd.errors.ParserError) as error:
            mo.stop(True, mo.callout(f"{domain}: {error}", kind="warn"))
        distribution = class_distribution(splits)
        max_gap = distribution.difference_pp.abs().max()
        counts = (
            audit.groupby(["status", "detected_language"])
            .size()
            .rename("reviews")
            .reset_index()
        )
        flagged = audit.loc[~audit.status.eq("spanish")]
        mo.output.replace(
            mo.vstack(
                [
                    mo.md(
                        f"### {domain.title()} · language review\nAudited **{len(audit):,} / {len(frame):,}** input rows; retained **{language_info['retained_rows']:,}**, excluded **{language_info['excluded_rows']:,}**. A sample describes only the reviewed rows."
                    ),
                    mo.ui.table(
                        counts,
                        selection=None,
                        label="Detected languages and review flags",
                    ),
                    mo.ui.table(
                        flagged.head(100).round(3),
                        selection=None,
                        label="Flagged texts · first 100",
                    ),
                    mo.download(
                        flagged.to_csv(index=False).encode(),
                        filename=f"{domain}-language-review.csv",
                        label="Download all audited flags",
                    ),
                    mo.ui.table(
                        distribution.round(2),
                        selection=None,
                        label="Class proportions after the selected language policy",
                    ),
                    mo.callout(
                        f"Maximum class deviation: {max_gap:.2f} percentage points. Every split includes negative, neutral and positive; rounding affects small classes.",
                        kind="success" if max_gap <= 5 else "warn",
                    ),
                ]
            )
        )
        return splits, source_hash, language_info

    return (load_and_preview,)


@app.cell
def stores_preview(
    audit_full,
    audit_stores,
    language_threshold,
    load_and_preview,
    min_letters,
    percentages,
    reload_data,
    spanish_only,
    split_seed,
    stores_file,
    stores_path,
):
    _ = reload_data.value
    stores_splits, stores_hash, stores_language_audit = load_and_preview(
        stores_file,
        stores_path.value,
        "stores",
        percentages,
        int(split_seed.value),
        audit_stores.value,
        audit_full.value,
        float(language_threshold.value),
        int(min_letters.value),
        spanish_only.value,
    )
    return stores_hash, stores_language_audit, stores_splits


@app.cell
def social_preview(
    audit_full,
    audit_social,
    language_threshold,
    load_and_preview,
    min_letters,
    percentages,
    reload_data,
    social_file,
    social_path,
    spanish_only,
    split_seed,
):
    _ = reload_data.value
    social_splits, social_hash, social_language_audit = load_and_preview(
        social_file,
        social_path.value,
        "social",
        percentages,
        int(split_seed.value),
        audit_social.value,
        audit_full.value,
        float(language_threshold.value),
        int(min_letters.value),
        spanish_only.value,
    )
    return social_hash, social_language_audit, social_splits


@app.cell
def parameter_controls(mo):
    def controls():
        return mo.ui.dictionary(
            {
                "training_mode": mo.ui.dropdown(
                    options={
                        "Head only": "head",
                        "Last encoder blocks + head": "last_n",
                        "Full fine-tuning": "full",
                    },
                    value="Head only",
                    label="Trainable layers",
                ),
                "last_layers": mo.ui.number(
                    start=1,
                    stop=12,
                    step=1,
                    value=1,
                    label="Final encoder blocks (last_n mode only)",
                ),
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
                "## 3. Choose layers and train\nEpochs are passes over the training data. Batch size is the number of reviews per update. Learning rate controls the update size. The seed makes runs reproducible.\n\nThe best checkpoint is selected by validation macro F1. Test data is reserved for final evaluation and the original-model comparison. Model files are logged to the workspace's configured artifact storage."
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
    def execute(
        splits, source_hash, domain, percentages, parameters, split_seed, language_audit
    ):
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
                language_audit=language_audit,
            )

    return (execute,)


@app.cell
def stores_training(
    execute,
    mo,
    percentages,
    split_seed,
    stores_hash,
    stores_language_audit,
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
        stores_language_audit,
    )
    return (stores_result,)


@app.cell
def social_training(
    execute,
    mo,
    percentages,
    social_hash,
    social_language_audit,
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
        social_language_audit,
    )
    return (social_result,)


@app.cell
def result_helper(mo, pd):
    import plotly.express as px
    import plotly.graph_objects as go

    def show_result(result):
        metrics = result["metrics"]
        labels = metrics["label_order"]
        comparison = []
        for name, report in [
            ("Original", metrics["baseline_test"]),
            ("Adapted", metrics["test"]),
        ]:
            comparison.append(
                {
                    "model": name,
                    "macro_f1": report["macro avg"]["f1-score"],
                    "weighted_f1": report["weighted avg"]["f1-score"],
                    "accuracy": report["accuracy"],
                }
            )
        per_class = pd.DataFrame(
            {label: metrics["test"][label] for label in labels}
        ).T.reset_index(names="class")
        delta = (
            metrics["test"]["macro avg"]["f1-score"] - metrics["baseline_test_macro_f1"]
        )
        history = pd.DataFrame(metrics["history"])
        matrix = go.Figure(
            go.Heatmap(
                z=metrics["confusion_matrix"],
                x=labels,
                y=labels,
                text=metrics["confusion_matrix"],
                texttemplate="%{text}",
                colorscale="Blues",
            )
        )
        matrix.update_layout(
            title="Test confusion matrix · counts",
            xaxis_title="Predicted",
            yaxis_title="Actual",
            yaxis_autorange="reversed",
            height=380,
        )
        mo.output.replace(
            mo.vstack(
                [
                    mo.md(
                        f"## 4. {result['domain'].title()} · evaluation\nMode: **{metrics['training_mode']}** · encoder blocks trained: **{metrics['encoder_blocks_trained']}**\n\nTrainable parameters: **{metrics['trainable_parameters']:,} / {metrics['total_parameters']:,} ({metrics['trainable_percent']:.2f}%)**\n\nBest epoch: **{metrics['best_epoch']}** · device: `{metrics['device']}`"
                    ),
                    mo.ui.table(
                        pd.DataFrame(comparison).round(4),
                        selection=None,
                        label="Same held-out test set · original versus adapted",
                    ),
                    mo.callout(
                        f"Test macro F1 change: {delta:+.4f}. A positive value is an improvement on this test set, not a guarantee on future reviews.",
                        kind="success" if delta > 0 else "warn",
                    ),
                    mo.ui.table(
                        per_class.round(4),
                        selection=None,
                        label="Precision, recall, F1 and support by class",
                    ),
                    mo.md(
                        "Precision measures how often predictions of a class are correct. Recall measures how many actual examples of that class are found. F1 balances precision and recall; support is the number of true test examples. Small support makes the estimate less stable."
                    ),
                    mo.hstack(
                        [
                            mo.ui.plotly(
                                px.line(
                                    history,
                                    x="epoch",
                                    y="loss",
                                    markers=True,
                                    title="Training loss",
                                )
                            ),
                            mo.ui.plotly(
                                px.line(
                                    history,
                                    x="epoch",
                                    y="validation_macro_f1",
                                    markers=True,
                                    title="Validation macro F1",
                                )
                            ),
                        ]
                    ),
                    mo.ui.plotly(matrix),
                    mo.md(
                        f"MLflow run: `{result['mlflow_run_id']}`\n\nZenML run: `{result['zenml_run_id']}`\n\nLocal model: `{result['output']}/model`\n\nLayer names, language-audit settings and counts, evaluation and model files are saved with the experiment. Select the completed local model in Workflow 01 to classify reviews."
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
