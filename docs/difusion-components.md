# Componentes para difusión

El transporte local consume copias verificadas del corpus histórico y sus embeddings. La identidad S3 conservada en los manifiestos describe su procedencia; estos comandos no descargan el dataset.

## Sentimiento adaptado de tiendas

Se fija `Banamex-voc-sentiment/voc-robertuito-sentiment-stores-es` en la revisión v3 `ff48c25f016499fc40d910f2a2a50072c706cdee`. La selección responde al acuerdo del equipo; no implica superioridad en todas las métricas. El reporte documenta sus resultados y límites.

La preparación se comprueba sin inferencia:

```sh
bash scripts/difusion.sh voc-ml sentiment prepare-model --config config/analysis/sentiment-stores-v3.toml
```

En una instalación nueva se puede permitir la descarga explícitamente con `env HF_HUB_OFFLINE=0` después del wrapper y `--download` al final. El token permanece en el archivo de credenciales persistente, fuera de Git.

Prueba operativa pequeña (no evaluación de calidad ni muestra representativa):

```sh
bash scripts/difusion.sh voc-ml sentiment workflow --source local --local-inputs config/analysis/difusion-local-inputs.toml --sentiment-config config/analysis/sentiment-stores-v3.toml --platform app_store --platform google_play --limit 8 --json
```

La selección de plataformas precede al límite. El clasificador rechaza X/YouTube con este checkpoint hasta definir el modelo correspondiente al dominio social. Para el modelo original se usa `config/analysis/sentiment.toml`. Modelo, revisión, preprocesamiento y entorno forman la identidad de caché: cambiar de checkpoint no reutiliza predicciones del anterior.

Comprobación del 4 de noviembre: run `c7d39f6a-c7e6-4115-8a63-942a4ebe0228`, 8 clasificadas, 0 fallidas, seguimiento verificado en ZenML y MLflow. Los artefactos locales están en `data/setup/stores-v3-smoke.json` y `data/analysis-runs/`. No son métricas de generalización.

## Relevancia

El equipo no conserva el modelo exportado ni los CSV completos de anotación. Reconstruir el entrenamiento requiere recuperar las etiquetas guardadas en la libreta y completar las ausentes. No se utilizarán las métricas históricas como si correspondieran al nuevo artefacto. El filtro se aplica a X/YouTube; las reseñas de tiendas se conservan.

### Reconstrucción por CLI

El comando siguiente recupera las 100 etiquetas de X del notebook inspeccionado, comprueba texto e índice contra los IDs del corpus y prepara una **muestra nueva** de 105 textos únicos de YouTube (orden por ID y semilla 42). No ejecuta las celdas ni modifica el demo. El directorio de salida debe ser nuevo.

```sh
bash scripts/difusion.sh voc-ml relevance prepare-annotations --local-inputs config/analysis/difusion-local-inputs.toml --notebook ../voc/notebooks/nb/sandbox/relevance_embeddings_documentado.ipynb --output data/relevance/reconstruction
```

La preparación ya realizada recuperó 91 relevantes, 6 irrelevantes y 3 inciertos de X. Su autoría histórica no se pudo verificar; se conserva como tal. Los 105 casos nuevos de YouTube requieren revisión. En `data/relevance/reconstruction/` están `revision-youtube.html`, `revision-youtube.md` y `youtube-proposals.csv`: propuestas de Codex, no etiquetas humanas. No están en Git porque contienen textos del corpus.

Revisar `human_label` (`relevant`, `irrelevant`, `uncertain`), `human_reviewer`, `review_note` y `review_status=reviewed` en el CSV. El criterio es relación textual con la app móvil de Banamex, sin usar título del video ni conversación. Las opiniones generales sobre la app también son relevantes. La interpretación conservadora de casos ambiguos debe ser revisada; no se fuerza la distribución histórica de clases.

```sh
bash scripts/difusion.sh voc-ml relevance import-review --annotations data/relevance/reconstruction/annotations.csv --reviewed-csv data/relevance/reconstruction/youtube-proposals.csv --output data/relevance/reconstruction/annotations-reviewed.csv
bash scripts/difusion.sh voc-ml relevance train --local-inputs config/analysis/difusion-local-inputs.toml --annotations data/relevance/reconstruction/annotations-reviewed.csv --output data/relevance/model-v1
```

El entrenamiento rechaza etiquetas vacías, IDs/textos incompatibles, conflictos entre textos idénticos y estratos plataforma/clase con menos de cinco textos claros. Excluye inciertos y deduplica por `model_text` antes de dividir. Ajusta `StandardScaler` dentro de cada fold, seguido de `LinearSVC(C=1, class_weight="balanced", random_state=42, max_iter=10000)`. Usa cinco folds estratificados por plataforma/clase, semilla 42 y cálculo float64. Las predicciones fuera de muestra incluyen IDs y folds; se guardan métricas globales y por plataforma y un control de clase mayoritaria por plataforma aprendido dentro de cada fold. Esta reconstrucción fija SVM; no repite toda la búsqueda histórica de clasificadores.

Exporta coeficientes y escalado a JSON portable, verifica equivalencia con las predicciones del estimador ajustado y registra perfil, versiones, hashes, IDs y hashes de textos de entrenamiento. No hace falta deserializar un `joblib` en el consumidor. Los márgenes no son probabilidades. La validación evita duplicados exactos; no garantiza independencia semántica entre textos similares. Cualquier evaluación independiente posterior debe excluir los textos/IDs de entrenamiento y revisar cercanía semántica.

Aplicación tras obtener un modelo real (reemplazar `SHA256_DEL_MODELO` por el valor de `evaluation.json`):

```sh
bash scripts/difusion.sh voc-ml relevance apply --local-inputs config/analysis/difusion-local-inputs.toml --model data/relevance/model-v1/model.json --model-sha256 SHA256_DEL_MODELO --output data/relevance/decisions-v1
```

El resultado conserva cada ID, decisión, motivo y margen. Las tiendas pasan intactas. Aplicar sobre el corpus no es evaluar calidad y no elimina los datos originales. La conexión de estas decisiones con las configuraciones base/alternativa se completará al tener el filtro real y fijar la muestra experimental; todavía no se declara completada la comparación integrada.

### Primera reconstrucción ejecutada — 4 de noviembre de 2026

El usuario confirmó que una persona revisó y aceptó las 105 propuestas de YouTube sin cambios. Se conservan las propuestas originales; `youtube-reviewed.csv` registra las decisiones y `human-review.json` la confirmación. No se atribuye una anotación ciega independiente ni una identidad individual que no fue proporcionada.

La importación y el entrenamiento por CLI finalizaron: 205 anotaciones, 45 inciertas excluidas y 160 textos distintos (96 relevantes y 64 irrelevantes). Recarga y predicciones portables verificadas. Modelo `data/relevance/model-v1/model.json`, SHA-256 `f8e24c1403a1103d4384863687d33308a343eef6064279a5bf5f612842b203d8`.

| Medida OOF | SVM reconstruido | Control por plataforma |
|---|---:|---:|
| Accuracy | 0,9125 | 0,93125 |
| Balanced accuracy | 0,90885 | 0,92708 |

F1 relevante del SVM: 0,92708. Matriz global `[[57, 7], [7, 89]]`, orden irrelevante/relevante. En X la matriz es `[[0, 6], [6, 85]]`; en YouTube `[[57, 1], [1, 4]]`. Hay solo seis negativos de X y cinco positivos de YouTube. El buen agregado **no demuestra ventaja sobre el control**; el modelo permanece como candidato experimental. No se cambió la configuración después de observar estos resultados.

La aplicación operativa a 80.079 registros conserva 78.476 y marca 1.603 para exclusión: 420 de X y 1.183 de YouTube. Todas las 72.198 reseñas de tiendas pasan intactas. Son decisiones del clasificador, no irrelevancias confirmadas; esta aplicación incluye entrenamiento y no constituye evaluación independiente. No se borró el corpus ni se ejecutó todavía la comparación BERTopic/sentimiento.

La evidencia agregada, hashes y comprobaciones quedan versionados en [relevance-v1.json](evidence/relevance-v1.json). Los textos, decisiones individuales y coeficientes permanecen en `data/` persistente, fuera de Git. La siguiente comparación debe fijar su muestra inicial y registrar exclusiones sin ocultar esta limitación.

### Comparación adicional con regresión logística

A petición del equipo se entrenó una alternativa con `StandardScaler` + `LogisticRegression(C=1, class_weight="balanced", solver="lbfgs", max_iter=5000, random_state=42)`. Conserva los 160 textos y **exactamente los mismos folds**, comprobados por ID, texto, etiqueta y asignación. El escalado se ajustó dentro de cada fold. No se ajustaron parámetros para mejorar los resultados observados.

```sh
bash scripts/difusion.sh voc-ml relevance train --local-inputs config/analysis/difusion-local-inputs.toml --annotations data/relevance/reconstruction/annotations-reviewed.csv --classifier logreg --reference-validation data/relevance/model-v1/validation.csv --output data/relevance/model-logreg-v1
```

| Modelo | Accuracy | Balanced accuracy | Precisión relevante | Recall relevante | F1 relevante |
|---|---:|---:|---:|---:|---:|
| SVM | 0,9125 | 0,9089 | 0,9271 | 0,9271 | 0,9271 |
| Regresión logística | 0,9250 | 0,9167 | 0,9200 | 0,9583 | 0,9388 |
| Control por plataforma | 0,9313 | 0,9271 | 0,9381 | 0,9479 | 0,9430 |

La logística dejó pasar ocho irrelevantes y perdió cuatro relevantes; SVM dejó pasar siete y perdió siete. Se conserva **provisionalmente SVM** por el criterio previo de reducir falsos positivos, a costa de perder tres relevantes más para evitar un irrelevante adicional. La logística es preferible si se prioriza recuperar comentarios útiles. Esta elección operativa no equivale a superioridad estadística: ambos fallaron los seis negativos de X y ninguno superó al control en accuracy/balanced accuracy. La selección usa la misma validación exploratoria, no un test independiente.

El modelo logístico también se exportó y recargó con decisiones equivalentes; no se volvió a aplicar al corpus completo ni se sustituyó el SVM. Los márgenes exportados son valores de decisión (logits en logística), no probabilidades calibradas. Evidencia verificable: [relevance-comparison-v1.json](evidence/relevance-comparison-v1.json). Once pruebas de relevancia pasan, incluida identidad de folds y rechazo de referencias alteradas.
