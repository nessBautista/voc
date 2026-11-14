import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium")


@app.cell
def intro():
    import marimo as mo
    mo.md("""
    # Comparación de RoBERTuito en reseñas de tiendas

    **Original · ajuste completo (v2) · ajuste parcial (transfer learning).**
    Adaptación de `workflow05_stores_comparison.py` para los drafts de VOC Difusión.
    La comparación usa **predicciones guardadas**, no vuelve a entrenar ni consulta APIs.

    Recorrido: localizar archivos → verificar la prueba común → comparar F1 y errores →
    explorar reseñas → examinar la silueta de las probabilidades, si resulta útil.
    Los nombres de los ajustes provienen del archivo original. No se presupone que el
    ajuste parcial sea el mismo checkpoint de tiendas v3 citado en el artículo.
    """)
    return (mo,)


@app.cell
def imports(mo):
    from pathlib import Path
    import os
    import hashlib
    import numpy as np
    import pandas as pd
    import plotly.express as px
    from sklearn.metrics import classification_report, confusion_matrix, silhouette_score
    from sklearn import config_context
    from threadpoolctl import threadpool_limits
    labels = ["negative", "neutral", "positive"]
    probability_columns = ["p_negative", "p_neutral", "p_positive"]
    _notebook = Path(mo.notebook_location()).resolve()
    repo_root = next(p for p in [_notebook, *_notebook.parents] if (p / "pyproject.toml").exists() and (p / "src/voc").exists())
    default_data_root = Path(os.environ.get("VOC_DATA_DIR", repo_root / "data")) / "sentiment"
    return (
        Path,
        classification_report,
        config_context,
        confusion_matrix,
        default_data_root,
        hashlib,
        labels,
        np,
        pd,
        probability_columns,
        px,
        silhouette_score,
        threadpool_limits,
    )


@app.cell
def paths(default_data_root, mo):
    paths_form = mo.ui.dictionary({
        "root": mo.ui.text(value=str(default_data_root), label="Carpeta sentiment", full_width=True),
        "full": mo.ui.text(value="runs/20261016T072421Z-66a523ea/rated", label="Corrida de ajuste completo (relativa a sentiment)", full_width=True),
        "partial": mo.ui.text(value="tracked-runs/stores-8bb70adca23e/training", label="Corrida de ajuste parcial (relativa a sentiment)", full_width=True),
    }).form(submit_button_label="Cargar y verificar archivos", show_clear_button=False)
    mo.vstack([mo.md("""
    ## 1. Archivos de entrada

    Configura las rutas y pulsa **Cargar y verificar archivos**. Cada corrida necesita
    `test.csv`. La corrida completa también necesita `baseline_test_predictions.csv`
    y `adapted_test_predictions.csv`; la parcial, `adapted_test_predictions.csv`.
    La lectura es local y no modifica estos archivos.
    """), paths_form])
    return (paths_form,)


@app.cell
def validation(
    classification_report,
    confusion_matrix,
    labels,
    np,
    pd,
    probability_columns,
):
    def read_indexed(path, columns):
        frame = pd.read_csv(path, usecols=["record_id", *columns], dtype={"record_id": str})
        if frame["record_id"].isna().any() or frame["record_id"].str.strip().eq("").any():
            raise ValueError(f"{path.name}: identificadores vacíos.")
        if frame[columns].isna().any().any():
            raise ValueError(f"{path.name}: valores faltantes.")
        return frame.set_index("record_id", verify_integrity=True)[columns].sort_index()

    def load_comparison(full_run, partial_run):
        test_frame = read_indexed(full_run / "test.csv", ["text", "label"])
        other_test = read_indexed(partial_run / "test.csv", ["text", "label"])
        if not test_frame.equals(other_test) or set(test_frame["label"]) != set(labels):
            raise ValueError("Las pruebas no tienen los mismos identificadores, textos y etiquetas, o faltan clases.")
        if test_frame["text"].str.strip().eq("").any():
            raise ValueError("La prueba contiene textos vacíos.")
        source_paths = {
            "Original": full_run / "baseline_test_predictions.csv",
            "Ajuste completo (v2)": full_run / "adapted_test_predictions.csv",
            "Ajuste parcial (transfer learning)": partial_run / "adapted_test_predictions.csv",
        }
        frames = {}
        for name, path in source_paths.items():
            frame = read_indexed(path, ["label", *probability_columns])
            scores = frame[probability_columns].to_numpy(dtype=float)
            if (not frame.index.equals(test_frame.index)
                or not frame["label"].isin(labels).all()
                or not np.isfinite(scores).all()
                or ((scores < 0) | (scores > 1)).any()
                or not np.allclose(scores.sum(axis=1), 1, atol=1e-5, rtol=0)):
                raise ValueError(f"{name}: predicciones incompatibles con la prueba común.")
            frames[name] = frame
        return test_frame, frames

    def build_metrics(test_frame, frames):
        summaries, classes, matrices = [], [], {}
        for name, frame in frames.items():
            report = classification_report(test_frame["label"], frame["label"], labels=labels, output_dict=True, zero_division=0)
            summaries.append({"Modelo": name, "F1 macro": report["macro avg"]["f1-score"],
                              "F1 ponderado": report["weighted avg"]["f1-score"], "Exactitud": report["accuracy"]})
            classes.extend({"Modelo":name,"Clase":label,"Precisión":report[label]["precision"],
                            "Recall":report[label]["recall"],"F1":report[label]["f1-score"],
                            "Soporte":int(report[label]["support"])} for label in labels)
            matrices[name] = confusion_matrix(test_frame["label"], frame["label"], labels=labels)
        return pd.DataFrame(summaries), pd.DataFrame(classes), matrices

    return build_metrics, load_comparison


@app.cell
def inventory(Path, default_data_root, mo, paths_form, pd):
    _settings = paths_form.value or {"root":str(default_data_root), "full":"runs/20261016T072421Z-66a523ea/rated", "partial":"tracked-runs/stores-8bb70adca23e/training"}
    sentiment_root = Path(_settings["root"]).expanduser()
    full_run = sentiment_root / _settings["full"]
    partial_run = sentiment_root / _settings["partial"]
    required_files = {"Prueba (completo)": full_run / "test.csv", "Original": full_run / "baseline_test_predictions.csv",
        "Ajuste completo": full_run / "adapted_test_predictions.csv", "Prueba (parcial)":partial_run / "test.csv",
        "Ajuste parcial": partial_run / "adapted_test_predictions.csv"}
    inventory = pd.DataFrame([{"Entrada":name,"Ruta":str(path),"Disponible":path.is_file()} for name,path in required_files.items()])
    all_files_available = all(path.is_file() for path in required_files.values())
    mo.vstack([mo.ui.table(inventory, selection=None),
        mo.callout("Archivos localizados. Se verificará que las tres predicciones correspondan a la misma prueba." if all_files_available else
                   "Faltan archivos históricos. Las métricas permanecen pendientes: no se sustituyen con datos inventados ni con los agregados del artículo.", kind="success" if all_files_available else "warn")])
    return all_files_available, full_run, partial_run, required_files


@app.cell
def load(
    all_files_available,
    full_run,
    hashlib,
    load_comparison,
    mo,
    partial_run,
    pd,
    required_files,
):
    mo.stop(not all_files_available, mo.md("**Comparación pendiente de los CSV indicados arriba.** Puedes revisar el código y las explicaciones aunque falten los datos."))
    try:
        test, predictions = load_comparison(full_run, partial_run)
    except (OSError, ValueError, TypeError) as _error:
        mo.stop(True, mo.callout(f"No se pudo validar la comparación: {_error}", kind="danger"))
    source_receipt = pd.DataFrame([{"Entrada":name,"Archivo":str(path),"SHA-256":hashlib.sha256(path.read_bytes()).hexdigest()} for name,path in required_files.items()])
    mo.vstack([mo.md(f"**{len(test):,} reseñas** con los mismos identificadores, textos y etiquetas en ambas corridas."),
        mo.ui.table(test["label"].value_counts().rename_axis("Clase").reset_index(name="Reseñas"), selection=None),
        mo.accordion({"Procedencia de los cinco archivos":mo.ui.table(source_receipt, selection=None)})])
    return predictions, test


@app.cell
def metrics_intro(mo):
    mo.md("""
    ## 2. Métricas y matrices de confusión

    El **F1 macro** da el mismo peso a las tres clases. El **F1 ponderado** pondera
    por la cantidad de reseñas. La exactitud resume aciertos, pero puede ocultar errores
    en clases poco frecuentes. Revisa por separado la clase neutral.

    Las etiquetas provienen de estrellas: son una aproximación al sentimiento textual.
    Las métricas se recalculan sobre las predicciones guardadas, sin reproducir el entrenamiento.
    """)
    return


@app.cell
def metrics(build_metrics, labels, mo, predictions, px, test):
    summary, by_class, matrices = build_metrics(test, predictions)
    _charts = []
    for _name, _counts in matrices.items():
        _chart = px.imshow(_counts, x=labels, y=labels, text_auto=True, color_continuous_scale="Blues",
            zmin=0, zmax=int(test["label"].value_counts().max()),
            labels={"x":"Predicción","y":"Etiqueta derivada de estrellas","color":"Reseñas"},title=_name,aspect="auto")
        _chart.update_layout(height=340,margin=dict(l=5,r=5,t=60,b=5))
        _charts.append(mo.ui.plotly(_chart))
    mo.vstack([mo.ui.table(summary.round(4),selection=None),mo.ui.table(by_class.round(4),selection=None),mo.hstack(_charts,widths="equal",wrap=True),
        mo.download(summary.to_csv(index=False).encode(),filename="comparacion-tiendas-metricas.csv",label="Descargar métricas")])
    return


@app.cell
def review_controls(mo):
    model_selector = mo.ui.dropdown(options=["Original","Ajuste completo (v2)","Ajuste parcial (transfer learning)"],value="Original",label="Modelo para revisar")
    case_selector = mo.ui.dropdown(options=["Todas las reseñas","Errores del modelo elegido","Desacuerdo entre modelos"],value="Desacuerdo entre modelos",label="Casos")
    mo.vstack([mo.md("## 3. Volver a los textos\nLos desacuerdos muestran dónde cambian las decisiones. No prueban por sí solos que uno de los modelos acierte."),mo.hstack([model_selector,case_selector])])
    return case_selector, model_selector


@app.cell
def review(case_selector, mo, model_selector, predictions, test):
    comparison = test.rename(columns={"label":"Etiqueta por estrellas"}).copy()
    for _name, _frame in predictions.items():
        comparison[_name] = _frame["label"]
    comparison["Desacuerdo"] = comparison[list(predictions)].nunique(axis=1)>1
    _visible = comparison
    if case_selector.value=="Errores del modelo elegido":
        _visible = comparison[comparison[model_selector.value]!=comparison["Etiqueta por estrellas"]]
    elif case_selector.value=="Desacuerdo entre modelos":
        _visible = comparison[comparison["Desacuerdo"]]
    mo.vstack([mo.md(f"**{len(_visible):,} casos** en esta selección. Puedes ordenar y buscar en la tabla."),
        mo.ui.table(_visible.reset_index(),selection=None,page_size=10)])
    return


@app.cell
def silhouette_intro(mo):
    _explanation = mo.md("""
    ## 4. Silueta de las probabilidades (exploración opcional)

    Aquí cada reseña es un punto de **tres probabilidades**, no un embedding MiniLM.
    La silueta compara su cercanía a los puntos de su grupo con los de otros grupos,
    usando distancia euclidiana. Va de −1 a 1.

    **Por clase predicha** utiliza las propias decisiones del modelo. Puede ser alta
    aunque existan errores. **Por etiqueta derivada de estrellas** examina la separación
    con esa referencia. Ninguna reemplaza F1 ni mide calibración de probabilidades.

    El archivo original calcula la silueta con toda la prueba, lo que implica muchas
    comparaciones por pares. Aquí puedes usar una misma submuestra aleatoria para los
    modelos (semilla 42), o seleccionar toda la prueba para reproducir ese alcance.
    """)
    silhouette_form = mo.ui.dictionary({
        "enabled":mo.ui.checkbox(value=False,label="Calcular silueta"),
        "scope":mo.ui.dropdown(options=["Submuestra común (máximo 2.000)","Toda la prueba"],value="Submuestra común (máximo 2.000)",label="Alcance"),
    }).form(submit_button_label="Aplicar cálculo",show_clear_button=False)
    mo.vstack([_explanation, silhouette_form])
    return (silhouette_form,)


@app.cell
def silhouette(
    config_context,
    mo,
    np,
    pd,
    predictions,
    probability_columns,
    px,
    silhouette_form,
    silhouette_score,
    test,
    threadpool_limits,
):
    mo.stop(not silhouette_form.value or not silhouette_form.value["enabled"],mo.md("Silueta desactivada. Activa el cálculo solo cuando quieras revisar este diagnóstico."))
    _silhouette_index = test.index if silhouette_form.value["scope"]=="Toda la prueba" else test.sample(n=min(2000,len(test)),random_state=42).index
    _silhouette_rows = []
    with config_context(working_memory=128), threadpool_limits(limits=2):
        for _name,_frame in predictions.items():
            _probabilities = _frame.loc[_silhouette_index,probability_columns].to_numpy()
            for _grouping,_classes in [("Clase predicha",_frame.loc[_silhouette_index,"label"]),("Etiqueta por estrellas",test.loc[_silhouette_index,"label"])]:
                _k = _classes.nunique()
                _valid = 1 < _k < len(_classes)
                _silhouette_rows.append({"Modelo":_name,"Agrupación":_grouping,"Reseñas":len(_silhouette_index),
                    "Silueta":silhouette_score(_probabilities,_classes,metric="euclidean") if _valid else np.nan,
                    "Estado":"Calculada" if _valid else "No definida: se requieren entre 2 y N−1 grupos"})
    silhouette_table = pd.DataFrame(_silhouette_rows)
    _silhouette_chart = px.bar(silhouette_table,x="Silueta",y="Modelo",color="Agrupación",barmode="group",orientation="h",text="Silueta",range_x=[-1,1])
    _silhouette_chart.update_traces(texttemplate="%{x:.4f}");_silhouette_chart.update_yaxes(autorange="reversed");_silhouette_chart.add_vline(x=0,line_color="gray")
    mo.vstack([mo.md(f"Alcance: **{silhouette_form.value['scope']}**, {len(_silhouette_index):,} reseñas comunes. La submuestra es exploratoria y no incluye intervalos de incertidumbre."),mo.ui.plotly(_silhouette_chart),mo.ui.table(silhouette_table.round(4),selection=None)])
    return


@app.cell
def closing(mo):
    mo.md("""
    ## Cómo interpretar esta comparación

    - Revisa F1 macro y errores por clase antes de escoger un modelo por exactitud.
    - Contrasta los desacuerdos con el texto. Las estrellas no equivalen siempre a una etiqueta humana de sentimiento.
    - Una silueta alta de las propias predicciones no demuestra mejores clasificaciones.
    - Comprueba la identidad de los checkpoints y la separación entre entrenamiento y prueba antes de trasladar conclusiones al artículo.

    Esta libreta es un **draft de exploración independiente**. No modifica el reporte,
    la libreta principal de evidencia ni las predicciones guardadas.
    """)
    return


if __name__ == "__main__":
    app.run()
