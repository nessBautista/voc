# Evidencia para Resultados y validación

Estas libretas están ejecutadas y sus salidas guardadas. Se pueden consultar sin
repetir el pipeline. Sus exportaciones PNG/PDF, CSV y JSON viven en `exports/`,
con carpetas por libreta y fecha y hashes en cada `manifest.json`.
`exports/<nombre>/latest.json` indica la última exportación completa.

## Orden de revisión

| Libreta | Qué revisar | Qué no demuestra |
| --- | --- | --- |
| [comparacion-base-alternativa.ipynb](comparacion-base-alternativa.ipynb) | Cobertura, distribución de tópicos, diagnóstico léxico, doce casos para revisión y transiciones de sentimiento sobre 987 textos comunes | Más tópicos o más positivos no equivalen a mejor calidad. NPMI tiene cobertura parcial y no valida funcionalidades. |
| [evaluacion-historica-robertuito.ipynb](evaluacion-historica-robertuito.ipynb) | Macro-F1/accuracy, F1 por clase, matriz de v3 y curva del entrenamiento publicado | No reproduce entrenamiento; no evalúa la muestra nueva ni valida transferencia a redes sociales. |
| [evaluacion-filtro-relevancia.ipynb](evaluacion-filtro-relevancia.ipynb) | SVM, logística y control por plataforma sobre 160 textos OOF; revisión humana de 13 exclusiones, por separado | Selección sobre la misma validación; la revisión de descartes no permite calcular recall global. |

La comparación integrada usa los artefactos de `data/experiments/julaug-2026/`:
1.000 registros iniciales de julio–agosto, 987 retenidos, UMAP 5D, HDBSCAN `eom`,
KeyBERTInspired + MMR. Base usa Robertuito original; alternativa usa tiendas v3
para todas las fuentes. Se reutilizan los lectores verificados del proyecto;
`apoyo_validacion.py` contiene únicamente utilidades de presentación, diagnóstico
léxico, selección de revisión y exportación. No cambia el pipeline.

## Abrir y ejecutar

En JupyterLab abre `reports/difusion/` desde el directorio del clon. Si ya tienes
una libreta abierta antes de estos cambios, guarda tus modificaciones y recarga
la versión del disco antes de continuar. El servidor existente es accesible por
`http://localhost:2718`; su disponibilidad depende de que esté iniciado.

Para ejecutar por terminal, desde la raíz de `voc-difusion`, un comando por vez:

```bash
bash scripts/difusion.sh jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=240 reports/difusion/comparacion-base-alternativa.ipynb
bash scripts/difusion.sh jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=240 reports/difusion/evaluacion-historica-robertuito.ipynb
bash scripts/difusion.sh jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=240 reports/difusion/evaluacion-filtro-relevancia.ipynb
```

La libreta histórica de Robertuito es autosuficiente con `sources/`; las otras dos
requieren las copias locales verificadas bajo `data/`. Las salidas guardadas y
exportaciones versionadas se pueden leer en otro equipo sin esos datos. Ejecutar
las libretas no descarga pesos, no ajusta modelos, no predice sentimiento y no
solicita interpretaciones LLM. Escribe salidas nuevas y, en la primera ejecución,
la plantilla vacía de revisión.

## Revisión humana pendiente

Abre la sección **Revisión temática preparada: doce casos fijos** de la primera
libreta. Hay dos tópicos por banda de tamaño y sistema; las bandas son relativas
a cada sistema. Cada tarjeta contiene cinco textos distintos elegidos por hash
y hasta dos ejemplos guardados adicionales. Las reseñas se muestran completas.
La muestra de grupos no incluye necesariamente el mayor grupo; la sección previa
muestra también la evidencia de los tres mayores de cada sistema.

Completa [revision-manual/topicos.csv](revision-manual/topicos.csv) con los criterios
explicados en la libreta: coherencia, palabras clave, tipo de asunto, justificación,
revisor y fecha. **Los juicios están vacíos**, no se han generado puntuaciones
humanas. No cambies IDs, protocolo, sistema, banda ni tamaño. Tras guardar, ejecuta
la celda «Recargar después de guardar el CSV» y la exportación. También puedes
volver a ejecutar la libreta completa: nunca sobrescribe las anotaciones.

La sección **Registro de revision manual** conserva la revisión previa de las
13 exclusiones: diez irrelevantes y tres relevantes perdidos. Es una revisión
distinta, ya realizada y no ciega. Las apreciaciones anteriores sobre ejemplos
de tópicos no sustituyen esta nueva revisión estructurada.

## Qué se puede llevar al reporte

- `topicos-estructura.png` / `.pdf` y `topicos-resumen.csv`: estructura y cobertura.
- `sentimiento-transiciones.png` / `.pdf`: cambios de predicción, no confusión frente a verdad humana.
- `robertuito-metricas.*` y `robertuito-confusion-v3.*`: desempeño histórico sobre referencia de estrellas.
- `relevancia-oof.*`: reconstrucción, distinta del filtro histórico de 168 casos.
- `relevancia-exclusiones-humanas.*`: solo los 13 descartados revisados.
- `casos-topicos.json` y `revision-topicos.csv`: evidencia y juicios; indicar siempre si siguen pendientes.

Antes de insertar una figura, usar el `latest.json` correspondiente, verificar
el manifiesto, copiar el archivo a `figuras/` del artículo y su recibo a
`evidencia/`. La leyenda debe declarar población y límites. Las exportaciones no
actualizan el PDF automáticamente. Se mantiene el reporte mientras los autores
revisan metodología; estas nuevas figuras quedan listas para su selección posterior.

### Diagnóstico léxico que requiere cautela

La diversidad cuenta términos distintos entre las primeras diez posiciones de
cada tópico. NPMI usa presencia por reseña sobre la misma muestra inicial de
1.000 registros, no ventanas deslizantes ni etiquetas humanas. Solo evalúa pares
con términos presentes y probabilidad conjunta menor que uno; coocurrencia cero
entre términos presentes vale −1. Los pares indefinidos se excluyen y contabilizan.

La cobertura observada de pares es aproximadamente 53,46% en base y 63,10% en
alternativa. Se detectaron 55/180 y 68/280 posiciones de términos que aparecen al
concatenar miembros del tópico, pero no dentro de una reseña de ese grupo.
Esto es un diagnóstico de bigramas y de la referencia léxica; no corrige resultados
ni demuestra que todos esos términos sean inútiles. Las medias de NPMI no deben
usarse como ranking entre los sistemas. Se conserva el detalle en
`presencia-terminos.csv` y `topicos-lexico.csv`.

La referencia de la fórmula y sus diferencias respecto de Gensim están en la
libreta. El resultado defendible es distinguir asociación léxica, estructura del
agrupamiento y juicio humano de utilidad, no reunirlos en una sola «accuracy».

## Procedencia y comprobaciones

`sources/robertuito-tiendas-v3-evaluation.json` es una copia exacta del archivo
agregado del checkpoint inmutable indicado en `sources/provenance.json`.
No incluye pesos ni credenciales. La libreta verifica su SHA-256 y reconcilia la
matriz de v3 con precisión, recall, F1, soportes y accuracy. No inventa una matriz
para el original, ausente en ese archivo.

Las métricas OOF se recalculan desde predicciones guardadas y se contrastan con
el recibo del CLI, verificando igualdad de IDs, objetivos y folds. La comparación
integrada verifica uniones por ID, igualdad de textos comunes y conservación de
la revisión humana previa. Hay pruebas de los casos límite de NPMI, selección
determinista y preservación de anotaciones:

```bash
bash scripts/difusion.sh pytest -q reports/difusion/test_apoyo_validacion.py
```

## Cobertura mensual del corpus

`cobertura-del-corpus.ipynb` lee los 80.079 registros preparados, verifica su hash contra el manifiesto y exporta barras apiladas por mes y plataforma. La fecha es la del comentario, no la de descarga. Requiere la copia local `data/inputs/corpus/`; no descarga datos ni ejecuta modelos. Los resultados ejecutados pueden consultarse directamente. Las exportaciones fechadas incluyen CSV, PNG, PDF y procedencia en `exports/cobertura-del-corpus/`; `latest.json` identifica la última ejecución. Septiembre de 2026 se marca como parcial.

## Apoyo para c-TF-IDF (sección 3.6)

[ejemplo-ctfidf.ipynb](ejemplo-ctfidf.ipynb) está ejecutada. Reconstruye conteos y pesos c-TF-IDF sobre las asignaciones guardadas del modelo base; incluye dos grupos, un cálculo paso a paso y una comprobación contra `ClassTfidfTransformer`. No vuelve a ejecutar embeddings, agrupamiento, sentimiento ni LLM. No confundir estos pesos con los pesos finales de KeyBERTInspired/MMR. Las exportaciones deterministas con hashes y parámetros están en `exports/ejemplo-ctfidf/`.

```bash
bash scripts/difusion.sh jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=180 reports/difusion/ejemplo-ctfidf.ipynb
```

## Completar los marcadores de evidencia de la sección 4

Las siguientes libretas están ejecutadas y guardadas. No requieren volver a
correr el pipeline ni pagar solicitudes. Su preparación no sustituye las
anotaciones humanas pendientes.

| Libreta | Componente disponible | Trabajo que falta |
| --- | --- | --- |
| [revision-topicos.ipynb](revision-topicos.ipynb) | Doce tarjetas, rúbrica y tabla de progreso; conserva los casos originales | Completar [topicos.csv](revision-manual/topicos.csv). La tabla de calidad se genera cuando estén revisados los doce grupos. |
| [validacion-sentimiento-muestra.ipynb](validacion-sentimiento-muestra.ipynb) | Selección fija de 60 textos comunes, ficha sin predicciones y cálculo de métricas/matrices preparado | Anotar [sentimiento.csv](revision-manual/sentimiento.csv) usando [las fichas](revision-manual/sentimiento-fichas.html). No hay métricas humanas nuevas todavía. |
| [revision-interpretacion-llm.ipynb](revision-interpretacion-llm.ipynb) | Entrada/respuesta histórica trazable, cotejo asistido, tabla y figura PNG/PDF | Revisar su utilidad editorial. Es una ilustración del demo, no un resultado de la comparación integrada ni una validación humana. |

### Revisión de sentimiento

Las 60 reseñas se distribuyen proporcionalmente entre App Store (3), Google
Play (53) y X (4), mediante restos mayores y orden SHA-256 por ID con semilla 42.
No se seleccionan por predicción ni por estrellas. Los pocos casos sociales no
permiten validar por separado el desempeño en redes. Se mantiene la unidad
registro: textos repetidos con IDs distintos pueden aparecer.

1. Abre `revision-manual/sentimiento-fichas.html` sin consultar predicciones.
2. En `sentimiento.csv`, completa `sentimiento_humano`, `justificacion`, `revisor`
   y `fecha`. Usa `negative`, `neutral`, `positive` o `uncertain` según la rúbrica.
3. Conserva las columnas de identidad y el texto. Declara en la justificación
   cualquier exposición previa a las predicciones.
4. Ejecuta la libreta nuevamente. Preserva las anotaciones; se detiene ante una
   selección alterada. Solo calcula métricas cuando todos los casos tengan revisión.

Los inciertos se contabilizan y excluyen de las métricas de ambos modelos sobre
los mismos textos. Se exportan accuracy, macro-F1 con tres clases fijas,
precisión/recall/F1/soporte por clase y matrices de confusión. No se presentan como
prueba independiente: faltan los IDs de entrenamiento para descartar solapamiento.
Una referencia de un revisor tampoco mide acuerdo entre anotadores.

```bash
bash scripts/difusion.sh jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=180 reports/difusion/revision-topicos.ipynb
bash scripts/difusion.sh jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=180 reports/difusion/validacion-sentimiento-muestra.ipynb
bash scripts/difusion.sh jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=180 reports/difusion/revision-interpretacion-llm.ipynb
bash scripts/difusion.sh pytest -q reports/difusion/test_apoyo_revision.py reports/difusion/test_apoyo_validacion.py
```

Cada libreta exporta en `exports/<nombre>/` con `latest.json` y hashes. La de
interpretación puede ejecutarse con la instantánea versionada en `sources/`;
la de tópicos reutiliza la exportación congelada de la comparación. La de
sentimiento requiere los análisis locales para recalcular la selección.
`verificacion-revisiones.json` registra esta preparación. Los CSV editables no
se sobrescriben; las exportaciones son copias fechadas, no archivos para anotar.
