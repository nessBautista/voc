# Comparación de RoBERTuito en reseñas de tiendas

Adaptación del archivo `workflow05_stores_comparison.py` recibido en el contexto de WOR-228. El original ya era una libreta marimo. Esta versión elimina la dependencia de `voc_demo.settings` y permite indicar las rutas desde la interfaz.

## Abrir

Desde la raíz del vault o de `voc-difusion`:

```bash
just difusion-marimo
```

En el explorador de `reports/`, abre **`drafts/difusion/workflow05_stores_comparison.py`**. Usa el enlace que imprime el comando. También está disponible [la copia HTML de lectura](workflow05_stores_comparison.html), sin recalcular controles.

Para abrir directamente desde el repositorio:

```bash
bash scripts/difusion.sh marimo edit reports/drafts/difusion/workflow05_stores_comparison.py
```

## Datos pendientes

No se encontraron los cinco CSV históricos en los directorios revisados. La libreta abre y muestra su inventario, pero todavía no presenta métricas de estos tres modelos. Por defecto busca en `data/sentiment/`:

- `runs/20261016T072421Z-66a523ea/rated/test.csv`
- `runs/20261016T072421Z-66a523ea/rated/baseline_test_predictions.csv`
- `runs/20261016T072421Z-66a523ea/rated/adapted_test_predictions.csv`
- `tracked-runs/stores-8bb70adca23e/training/test.csv`
- `tracked-runs/stores-8bb70adca23e/training/adapted_test_predictions.csv`

Puedes configurar otra carpeta y los dos subdirectorios en la primera sección. Pulsa **Cargar y verificar archivos** después de editar las rutas.

Los archivos `test.csv` requieren `record_id`, `text` y `label`. Las predicciones requieren `record_id`, `label`, `p_negative`, `p_neutral` y `p_positive`. Las etiquetas admitidas son `negative`, `neutral` y `positive`.

Antes de calcular resultados, se verifica que las dos pruebas tengan los mismos identificadores, textos y etiquetas, que cada predicción cubra la prueba completa y que las probabilidades sean finitas, estén entre cero y uno y sumen uno. Se rechazan identificadores duplicados y valores faltantes.

## Qué permite revisar

1. F1 macro, ponderado, exactitud y precisión/recall/F1 por clase.
2. Las tres matrices de confusión con la misma escala.
3. Textos donde hay errores o desacuerdos entre modelos.
4. Silueta sobre las tres probabilidades, por clase predicha y por etiqueta de estrellas.

La silueta es opcional. Por defecto calcula una submuestra común de hasta 2.000 reseñas, con semilla 42. Selecciona **Toda la prueba** para reproducir el alcance del archivo original, con mayor costo de cómputo. No es una métrica de aciertos ni de calibración y no se calcula sobre embeddings de BERTopic.

Los nombres «ajuste completo (v2)» y «ajuste parcial» vienen del original. La identidad del segundo checkpoint todavía debe verificarse antes de asociarlo a tiendas v3. Las métricas agregadas del artículo no sustituyen estos CSV ni prueban esa identidad.

## Validación y límites

Se probó la sesión reactiva con fixtures sintéticos temporales: alineación por ID, métricas, matrices, silueta y rechazo de textos inconsistentes, probabilidades inválidas e IDs duplicados. Los fixtures no forman parte de la libreta ni representan resultados del experimento. Se restauraron las rutas reales antes de exportar.

No se entrena ni descarga ningún modelo y no se hacen llamadas de API. Esta libreta es un draft, separado del artículo y de su libreta principal de evidencia.
