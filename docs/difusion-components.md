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
