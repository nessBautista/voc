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
