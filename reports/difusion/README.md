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
