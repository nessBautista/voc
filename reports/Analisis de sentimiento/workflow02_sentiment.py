# /// script
# requires-python = ">=3.12,<3.13"
# ///
"""Two independent RoBERTuito experiments: star-rated and social reviews."""

# Marimo uses explicit cell returns to declare dependencies.
# ruff: noqa: B018, PLR1711

import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium")


@app.cell
def imports():
    import uuid
    from datetime import datetime, timezone
    from pathlib import Path

    import marimo as mo
    import pandas as pd

    from voc_demo.sentiment.adaptation import predict
    from voc_demo.sentiment.experiments import (
        load_experiment_data,
        load_trained_model,
        train_experiments,
    )
    from voc_demo.settings import data_root, model_root

    return (
        Path,
        data_root,
        datetime,
        load_experiment_data,
        load_trained_model,
        mo,
        model_root,
        pd,
        predict,
        timezone,
        train_experiments,
        uuid,
    )


@app.cell(hide_code=True)
def introduction(mo):
    mo.md("""
    # Sentimiento · dos entrenamientos

    1. **Tiendas:** `rated_reviews.csv`, con etiquetas por estrellas: 1–2 negativas,
       3 neutrales y 4–5 positivas. Se respeta su columna `split`.
    2. **X y YouTube:** `social_reviews_to_label.csv`, con tus etiquetas manuales.
       Si no tiene `split`, se divide 60 % entrenamiento, 20 % validación y 20 % prueba.

    Los entrenamientos se ejecutan uno después del otro. **Ambos parten del
    RoBERTuito original** de `workflow01.py`; cada uno guarda su propio modelo.
    Se selecciona la época por F1 macro de validación y se compara con el modelo
    original en la prueba de ese dataset. No se mezclan los conjuntos.

    Los CSV deben tener `record_id,text,label,platform`. Las etiquetas son
    `negative`, `neutral`, `positive`. Si etiquetaste en Numbers, exporta el archivo
    como **CSV** antes de continuar. No requiere embeddings, temas ni S3.
    """)
    return


@app.cell
def configuration(data_root, mo):
    rated_path = mo.ui.text(
        value=str(data_root() / "sentiment/rated_reviews.csv"), label="CSV de tiendas"
    )
    social_path = mo.ui.text(
        value=str(data_root() / "sentiment/social_reviews_to_label.csv"),
        label="CSV social etiquetado",
    )
    epochs = mo.ui.number(start=1, stop=10, value=3, label="Épocas por entrenamiento")
    batch_size = mo.ui.number(start=1, stop=64, value=8, label="Reseñas por lote")
    mo.vstack(
        [
            rated_path,
            social_path,
            mo.hstack([epochs, batch_size]),
            mo.md(
                "El conjunto de tiendas tiene más de 43 mil reseñas de entrenamiento. En CPU puede tardar horas. El modelo se descarga en el primer uso."
            ),
        ]
    )
    return batch_size, epochs, rated_path, social_path


@app.cell
def data_preview(Path, load_experiment_data, mo, pd, rated_path, social_path):
    try:
        experiment_data = load_experiment_data(
            {"rated": Path(rated_path.value), "social": Path(social_path.value)}
        )
    except (ValueError, OSError, pd.errors.ParserError) as _error:
        mo.stop(True, mo.callout(str(_error), kind="warn"))
    _rows = []
    for _name, _dataset in experiment_data.items():
        for _split, _frame in _dataset["splits"].items():
            _counts = _frame.label.value_counts()
            _rows.append(
                {
                    "dataset": _name,
                    "partición": _split,
                    "reseñas": len(_frame),
                    **{
                        _label: int(_counts.get(_label, 0))
                        for _label in ("negative", "neutral", "positive")
                    },
                }
            )
    mo.vstack(
        [mo.md("## Datos listos para entrenar"), mo.ui.table(pd.DataFrame(_rows))]
    )
    return (experiment_data,)


@app.cell
def training_control(experiment_data, mo):
    _ = experiment_data
    train_button = mo.ui.run_button(label="Ejecutar los dos entrenamientos")
    train_button
    return (train_button,)


@app.cell
def training(
    batch_size,
    data_root,
    datetime,
    epochs,
    experiment_data,
    mo,
    model_root,
    timezone,
    train_button,
    train_experiments,
    uuid,
):
    mo.stop(
        not train_button.value,
        mo.md("Revisa las particiones y pulsa **Ejecutar los dos entrenamientos**."),
    )
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    experiment_output = data_root() / "sentiment/runs" / run_id
    with mo.status.spinner(
        title="Entrenando tiendas y después redes sociales…"
    ) as _status:

        def _progress(name):
            _status.update(
                title=f"Entrenando y evaluando: {'tiendas (1/2)' if name == 'rated' else 'X y YouTube (2/2)'}"
            )

        experiment_results = train_experiments(
            experiment_data,
            experiment_output,
            model_root() / "sentiment",
            epochs=int(epochs.value),
            batch_size=int(batch_size.value),
            on_progress=_progress,
        )
    mo.md(
        f"**Dos entrenamientos completados.** Resultados en `{experiment_output}`; subcarpetas `rated/` y `social/`."
    )
    return (experiment_results,)


@app.cell
def evaluation(experiment_results, mo, pd):
    _comparison, _details = [], []
    for _name, _result in experiment_results.items():
        _metrics = _result["metrics"]
        _comparison.extend(
            [
                {
                    "dataset": _name,
                    "modelo": "Original",
                    "F1 macro validación": _metrics["baseline_validation_macro_f1"],
                    "F1 macro prueba": _metrics["baseline_test_macro_f1"],
                },
                {
                    "dataset": _name,
                    "modelo": "Adaptado",
                    "F1 macro validación": _metrics["validation_macro_f1"],
                    "F1 macro prueba": _metrics["test"]["macro avg"]["f1-score"],
                },
            ]
        )
        _details.append(
            mo.vstack(
                [
                    mo.md(
                        f"### {_name} · mejor época {_metrics['best_epoch']}\nModelo guardado en `{_result['output']}/model`."
                    ),
                    mo.ui.table(
                        pd.DataFrame(_metrics["history"]).round(3),
                        label="Entrenamiento por época",
                    ),
                    mo.ui.table(
                        pd.DataFrame(_metrics["test"]).T.round(3),
                        label="Evaluación en prueba",
                    ),
                    mo.ui.table(
                        pd.DataFrame(
                            _metrics["confusion_matrix"],
                            index=_metrics["label_order"],
                            columns=_metrics["label_order"],
                        ),
                        label="Confusión: filas reales, columnas predichas",
                    ),
                ]
            )
        )
    mo.vstack(
        [
            mo.md(
                "## Resultados\nCada modelo se evalúa en la prueba de su propio dataset. Revisa su mejora frente al original y sus resultados por clase."
            ),
            mo.ui.table(pd.DataFrame(_comparison).round(3)),
            *_details,
        ]
    )
    return


@app.cell
def inference_controls(data_root, mo):
    inference_model = mo.ui.dropdown(
        options={"Tiendas": "rated", "X y YouTube": "social"},
        value="Tiendas",
        label="Modelo para clasificar",
    )
    inference_path = mo.ui.text(
        value=str(data_root() / "sentiment/reviews_to_classify.csv"),
        label="CSV de nuevas reseñas: record_id,text",
    )
    classify_button = mo.ui.run_button(label="Clasificar con el modelo seleccionado")
    mo.vstack(
        [
            mo.md("## Clasificar nuevas reseñas"),
            inference_model,
            inference_path,
            classify_button,
        ]
    )
    return classify_button, inference_model, inference_path


@app.cell
def inference(
    Path,
    classify_button,
    experiment_results,
    inference_model,
    inference_path,
    load_trained_model,
    mo,
    pd,
    predict,
):
    mo.stop(
        not classify_button.value,
        mo.md("Selecciona uno de los dos modelos y el CSV de nuevas reseñas."),
    )
    mo.stop(
        not Path(inference_path.value).is_file(),
        mo.md("No se encontró el CSV de nuevas reseñas."),
    )
    _output = Path(experiment_results[inference_model.value]["output"])
    _tokenizer, _model = load_trained_model(_output)
    _reviews = pd.read_csv(inference_path.value, dtype=str, keep_default_na=False)
    _predictions = predict(_reviews, _tokenizer, _model)
    _predictions.to_csv(_output / "predictions.csv", index=False)
    mo.vstack(
        [
            mo.md(
                f"Guardado en `{_output / 'predictions.csv'}`. Probabilidades sin calibrar."
            ),
            mo.ui.table(_predictions),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
