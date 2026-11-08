import marimo

__generated_with = "0.25.0"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def imports():
    import marimo as mo
    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    import json
    import importlib.metadata as metadata
    from sklearn.feature_extraction.text import CountVectorizer
    from bertopic.vectorizers import ClassTfidfTransformer

    return (
        ClassTfidfTransformer,
        CountVectorizer,
        json,
        metadata,
        mo,
        np,
        pd,
        plt,
    )


@app.cell(hide_code=True)
def introduction(mo):
    mo.md(r"""
    # c-TF-IDF: poner palabras a una caja de comentarios

    **Ya tenemos los grupos. Ahora queremos descubrir qué palabras los describen.**
    Imagina tres cajas: una contiene problemas de transferencias, otra problemas de acceso
    y otra elogios. Una palabra que se repite en una caja puede ser una buena pista;
    si abunda en todo el conjunto, suele ayudarnos menos a distinguirla.

    Recorreremos **textos → conteos → frecuencia normalizada → peso global → palabras clave**.
    Puedes cambiar los textos y observar qué se recalcula. Las cajas son asignaciones
    manuales de un ejemplo didáctico: aquí no ejecutamos HDBSCAN, embeddings, sentimiento ni un LLM.
    Estos resultados **no son métricas del experimento del reporte**.
    """)
    return


@app.cell(hide_code=True)
def controls(mo):
    box_a = mo.ui.text_area(value="La aplicación falla al hacer una transferencia\nNo puedo realizar una transferencia\nMi transferencia no sale de la aplicación", rows=4, full_width=True, label="Caja A · transferencias")
    box_b = mo.ui.text_area(value="No puedo entrar a la aplicación\nLa aplicación rechaza mi contraseña\nMi contraseña no funciona", rows=4, full_width=True, label="Caja B · acceso")
    box_c = mo.ui.text_area(value="La aplicación es excelente\nExcelente servicio y atención\nExcelente aplicación fácil de usar", rows=4, full_width=True, label="Caja C · elogios")
    use_stops = mo.ui.checkbox(value=True, label="Excluir palabras funcionales de la lista didáctica")
    use_bigrams = mo.ui.checkbox(value=False, label="Contar también pares de palabras (bigramas)")
    use_root = mo.ui.checkbox(value=False, label="Activar reduce_frequent_words: raíz cuadrada del TF")
    repeat_a = mo.ui.slider(start=1, stop=5, step=1, value=1, label="Copias de la caja A")
    transfer_b = mo.ui.slider(start=0, stop=12, step=1, value=0, label="Apariciones extra de ‘transferencia’ en B")
    mo.vstack([
     mo.md("## 1. Las cajas de entrada\nUna reseña por línea. Edita y sal del campo para aplicar el cambio. Cambiar el contenido no reasigna comentarios entre cajas."),
     mo.hstack([box_a, box_b, box_c], widths="equal"),
     mo.md("**Controles de preparación y experimentación**"),
     use_stops, use_bigrams, use_root, repeat_a, transfer_b,
     mo.md("La lista didáctica excluye `la, el, de, al, a, una, un, mi, es, y, para`. Conservamos **no**, porque puede ayudar a describir una queja. No es la lista completa del proyecto. Los controles extra solo modifican este ejemplo.")
    ])
    return (
        box_a,
        box_b,
        box_c,
        repeat_a,
        transfer_b,
        use_bigrams,
        use_root,
        use_stops,
    )


@app.cell(hide_code=True)
def calculate(
    ClassTfidfTransformer,
    CountVectorizer,
    box_a,
    box_b,
    box_c,
    mo,
    np,
    pd,
    repeat_a,
    transfer_b,
    use_bigrams,
    use_root,
    use_stops,
):
    toy_stop_words = ["la", "el", "de", "al", "a", "una", "un", "mi", "es", "y", "para"]
    group_names = ["A · transferencias", "B · acceso", "C · elogios"]
    source_texts = [box_a.value, box_b.value, box_c.value]
    review_lists = [[line.strip() for line in text.splitlines() if line.strip()] for text in source_texts]
    review_lists[0] = review_lists[0] * repeat_a.value
    group_documents = [" ".join(lines) for lines in review_lists]
    group_documents[1] += " transferencia" * transfer_b.value
    vectorizer = CountVectorizer(stop_words=toy_stop_words if use_stops.value else None, ngram_range=(1, 2) if use_bigrams.value else (1, 1), min_df=1)
    _analyzer = vectorizer.build_analyzer()
    mo.stop(any(not _analyzer(text) for text in group_documents), mo.md("⚠️ Escribe al menos un término reconocible en cada caja. Se ignoran tokens de un solo carácter y, si está activada, la lista de exclusiones."))
    count_matrix = vectorizer.fit_transform(group_documents)
    vocabulary = vectorizer.get_feature_names_out()
    counts = count_matrix.toarray()
    group_lengths = counts.sum(axis=1)
    global_frequencies = counts.sum(axis=0)
    mean_length = int(group_lengths.mean())
    normalized_tf = counts / group_lengths[:, None]
    idf_values = np.log1p(mean_length / global_frequencies)
    basic_weights = normalized_tf * idf_values
    root_weights = np.sqrt(normalized_tf) * idf_values
    selected_weights = root_weights if use_root.value else basic_weights
    reference_basic = ClassTfidfTransformer(reduce_frequent_words=False).fit_transform(count_matrix.copy()).toarray()
    reference_root = ClassTfidfTransformer(reduce_frequent_words=True).fit_transform(count_matrix.copy()).toarray()
    np.testing.assert_allclose(basic_weights, reference_basic, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(root_weights, reference_root, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(normalized_tf.sum(axis=1), 1.0)
    counts_table = pd.DataFrame(counts.T, columns=group_names)
    counts_table.insert(0, "Término", vocabulary)
    counts_table["Total f(t)"] = global_frequencies
    weights_table = pd.DataFrame(selected_weights.T, columns=group_names)
    weights_table.insert(0, "Término", vocabulary)
    return (
        basic_weights,
        counts,
        counts_table,
        global_frequencies,
        group_documents,
        group_lengths,
        group_names,
        idf_values,
        mean_length,
        normalized_tf,
        root_weights,
        selected_weights,
        source_texts,
        vocabulary,
        weights_table,
    )


@app.cell(hide_code=True)
def counts_view(
    counts_table,
    group_documents,
    group_lengths,
    group_names,
    mean_length,
    mo,
    vocabulary,
):
    mo.vstack([
     mo.md("## 2. Juntamos las reseñas y contamos\nCada caja se convierte en un documento agregado. Cada número cuenta **apariciones del término**, no reseñas que lo mencionan. No usamos los vectores MiniLM para este cálculo."),
     mo.accordion({name: mo.md(text) for name, text in zip(group_names, group_documents)}),
     mo.ui.table(counts_table, selection=None, page_size=15, label="Matriz de conteos · términos por caja"),
     mo.md(f"**Totales N(c):** A = {group_lengths[0]}, B = {group_lengths[1]}, C = {group_lengths[2]}. **Vocabulario:** {len(vocabulary)} términos. La media es {group_lengths.mean():.4f}; BERTopic usa su conversión entera: **A = {mean_length}**. Este símbolo A es una media, no el nombre de la primera caja."),
     mo.md("Con bigramas, N(c) cuenta unigramas **y** bigramas. Como concatenamos reseñas, algunos pares pueden cruzar su frontera; esto también ocurre en esta preparación del pipeline.")
    ])
    return


@app.cell(hide_code=True)
def choose_term(group_names, mo, vocabulary):
    focus_group = mo.ui.dropdown(options=group_names, value=group_names[0], label="Caja que queremos describir")
    focus_term = mo.ui.dropdown(options=list(vocabulary), value="transferencia" if "transferencia" in vocabulary else str(vocabulary[0]), searchable=True, label="Término para seguir el cálculo")
    mo.vstack([mo.md("## 3. Sigue una palabra, paso a paso"),mo.hstack([focus_group,focus_term])])
    return focus_group, focus_term


@app.cell(hide_code=True)
def formula_view(
    counts,
    focus_group,
    focus_term,
    global_frequencies,
    group_lengths,
    group_names,
    idf_values,
    mean_length,
    mo,
    normalized_tf,
    np,
    pd,
    selected_weights,
    use_root,
    vocabulary,
):
    group_index = group_names.index(focus_group.value)
    term_index = list(vocabulary).index(focus_term.value)
    n_tc = int(counts[group_index, term_index])
    N_c = int(group_lengths[group_index])
    f_t = int(global_frequencies[term_index])
    tf_value = float(normalized_tf[group_index, term_index])
    idf_value = float(idf_values[term_index])
    actual_tf = float(np.sqrt(tf_value)) if use_root.value else tf_value
    score_value = float(selected_weights[group_index, term_index])
    step_table = pd.DataFrame([
     {"Paso":"1 · Conteo n(t,c)","Cálculo":f"Apariciones de {focus_term.value} en {focus_group.value}","Valor":float(n_tc)},
     {"Paso":"2 · TF normalizado", "Cálculo":f"{n_tc} / {N_c}","Valor":tf_value},
     {"Paso":"3 · TF usado", "Cálculo":"Raíz cuadrada del TF" if use_root.value else "TF sin transformación", "Valor":actual_tf},
     {"Paso":"4 · IDF", "Cálculo":f"ln(1 + {mean_length} / {f_t})", "Valor":idf_value},
     {"Paso":"5 · Peso", "Cálculo":"TF usado × IDF", "Valor":score_value},
    ])
    mo.vstack([
     mo.md(r"""
    **TF pregunta:** ¿qué proporción de los términos de esta caja corresponde a esta palabra?
    Dividir por el total evita premiar un conteo grande solo por tener más texto.

    **IDF pregunta:** ¿cuánto abunda la palabra en el conjunto? Aquí usamos su número total
     de apariciones, **no** el número de cajas donde aparece. Un IDF más alto da más peso;
    no significa una probabilidad mayor.

    $$w(t,c)=\frac{n(t,c)}{N(c)}\ln\left(1+\frac{A}{f(t)}\right)$$

    Con la opción `reduce_frequent_words=True`, reemplazamos el TF por su raíz:

    $$w(t,c)=\sqrt{\frac{n(t,c)}{N(c)}}\ln\left(1+\frac{A}{f(t)}\right).$$
    """),
     mo.ui.table(step_table, selection=None, pagination=False, format_mapping={"Valor":"{:.6f}"}),
     mo.callout(f"Peso de «{focus_term.value}» en {focus_group.value}: {score_value:.6f}. Si no aparece en esa caja, su TF y su peso son cero.", kind="info")
    ])
    return (group_index,)


@app.cell(hide_code=True)
def rankings(
    group_names,
    mo,
    np,
    pd,
    plt,
    selected_weights,
    vocabulary,
    weights_table,
):
    top_rows = []
    for _i, _name in enumerate(group_names):
        for _j in np.argsort(-selected_weights[_i], kind="stable")[:8]:
            if selected_weights[_i, _j] > 0:
                top_rows.append({"Caja":_name,"Término":str(vocabulary[_j]),"Peso":float(selected_weights[_i,_j])})
    top_table = pd.DataFrame(top_rows)
    fig_rank, axes_rank = plt.subplots(1,3,figsize=(12,4.4),layout="constrained")
    for _i, _ax in enumerate(axes_rank):
        _part = top_table[top_table.Caja == group_names[_i]].iloc[::-1]
        _ax.barh(_part["Término"],_part["Peso"],color=["#27647d","#b66a23","#438365"][_i])
        _ax.set_title(group_names[_i]);_ax.set_xlabel("Peso c-TF-IDF")
        _ax.spines[["top","right"]].set_visible(False)
        _ax.set_xlim(0,float(selected_weights.max())*1.12)
    ranking_display = mo.vstack([
     mo.md("## 4. Las palabras que llevaríamos a la etiqueta de cada caja\nOrdenamos de mayor a menor peso; aquí mostramos hasta ocho términos presentes en cada caja. Los tres paneles comparten la escala horizontal. Las palabras todavía no forman un nombre ni una explicación completa del tópico."),
     mo.as_html(fig_rank),
     mo.accordion({"Ver todos los pesos":mo.ui.table(weights_table.round(6),selection=None,page_size=15)}),
     mo.callout("Comprobación: el cálculo manual coincide, para todos los términos y grupos, con ClassTfidfTransformer de BERTopic, tanto en la fórmula básica como con raíz cuadrada.",kind="success")
    ])
    plt.close(fig_rank)
    ranking_display
    return (top_table,)


@app.cell(hide_code=True)
def root_comparison(
    basic_weights,
    counts,
    focus_group,
    group_index,
    idf_values,
    mo,
    normalized_tf,
    pd,
    root_weights,
    vocabulary,
):
    comparison_table = pd.DataFrame({"Término":vocabulary,"Conteo":counts[group_index],"TF":normalized_tf[group_index],"IDF":idf_values,"Básico":basic_weights[group_index],"Con raíz":root_weights[group_index]})
    comparison_table = comparison_table[comparison_table.Conteo>0].sort_values("Básico",ascending=False)
    mo.vstack([
     mo.md("## 5. ¿Qué cambia con la raíz cuadrada?\nEn nuestro proyecto está activada. Para valores entre cero y uno, la raíz cuadrada **aumenta** el número, pero aumenta proporcionalmente más los pequeños. Comprime las diferencias entre frecuencias: no significa que todos los pesos finales disminuyan. Puede cambiar el orden de términos porque después se combina con IDF. Los pesos de las dos variantes no son probabilidades ni una medida de calidad del tópico."),
     mo.ui.table(comparison_table.round(6),selection=None,page_size=12,label=f"Comparación para {focus_group.value}")
    ])
    return


@app.cell(hide_code=True)
def experiments(mo):
    mo.md("""
    ## 6. Tres experimentos para construir intuición

    1. **Una palabra deja de ser exclusiva.** Selecciona «transferencia» en A. Sube sus apariciones extra en B. Observa f(t), A, IDF y el peso en A. No cambió el texto de A, pero su representación puede cambiar porque el cálculo usa todo el conjunto. También cambia A: revisa ambos valores, no atribuyas el resultado únicamente a f(t).
    2. **Una caja tiene más texto.** Devuelve el control anterior a cero. Sin bigramas, duplica A. Sus conteos se duplican y su TF permanece igual; el peso final puede variar porque cambian las frecuencias globales y la longitud media. Con bigramas aparecen además pares en las fronteras de concatenación.
    3. **Contar no es comprender.** Agrega «transferencia excelente» a C. El algoritmo no sabe si es un elogio o una queja; solo observa términos y frecuencias. Puede dar peso a una falta de ortografía o a vocabulario accidental.

    **Lo que obtenemos:** una lista ponderada de palabras o expresiones por grupo.
    No reasignamos reseñas, no evaluamos el sentimiento y no demostramos que un tópico sea útil.
    KeyBERTInspired y MMR actuarán después sobre la representación; el LLM interpretará las pistas resultantes.
    """)
    return


@app.cell(hide_code=True)
def exports(
    counts,
    global_frequencies,
    group_lengths,
    group_names,
    json,
    mean_length,
    metadata,
    mo,
    repeat_a,
    selected_weights,
    source_texts,
    top_table,
    transfer_b,
    use_bigrams,
    use_root,
    use_stops,
    vocabulary,
):
    experiment = {
     "scope":"Ejemplo didáctico sintético; no resultados del corpus",
     "texts":source_texts,
     "controls":{"copies_A":repeat_a.value,"extra_transferencia_B":transfer_b.value,"stop_words":use_stops.value,"bigrams":use_bigrams.value,"reduce_frequent_words":use_root.value},
     "terms":list(vocabulary),"group_names":group_names,"counts":counts.tolist(),"N":group_lengths.tolist(),"f":global_frequencies.tolist(),"A":mean_length,
     "weights":selected_weights.tolist(),
     "verification":"Ambas variantes coinciden con ClassTfidfTransformer; assert_allclose rtol=atol=1e-12",
     "versions":{package:metadata.version(package) for package in ["bertopic","scikit-learn","numpy","pandas","marimo"]},
    }
    mo.vstack([
     mo.md("## 7. Guarda lo que encontraste\nDescarga el estado del experimento para comparar otra configuración. Solo se escribe un archivo si pulsas descargar."),
     mo.hstack([mo.download(json.dumps(experiment,ensure_ascii=False,indent=2).encode(),filename="experimento-ctfidf.json",label="Descargar experimento JSON"),mo.download(top_table.to_csv(index=False).encode(),filename="palabras-ctfidf.csv",label="Descargar palabras CSV")]),
     mo.md("**Conexión con el reporte.** `reports/drafts/difusion/ejemplo-ctfidf.ipynb` reconstruye estos pesos sobre las 1.000 reseñas y las asignaciones guardadas del modelo base. Aquella libreta incluye bigramas, la lista de exclusiones del proyecto y la raíz cuadrada, además del grupo −1 para los casos sin asignación. Este laboratorio permite aislar el cálculo con tres cajas pequeñas; no sustituye esa evidencia.\n\n**Fuentes:** [explicación oficial](https://maartengr.github.io/BERTopic/getting_started/ctfidf/ctfidf.html) · [implementación de ClassTfidfTransformer](https://maartengr.github.io/BERTopic/api/ctfidf.html).")
    ])
    return


if __name__ == "__main__":
    app.run()
