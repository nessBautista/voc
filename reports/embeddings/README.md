# De una reseña a un embedding

Abre `produccion-de-embeddings.ipynb` desde `just jupyter-reports`, dentro de `embeddings/`. `notebooks/reports` es un enlace a `reports`, por lo que ambas rutas muestran el mismo archivo.

La libreta explica el mecanismo mediante seis frases inventadas: preparación del texto, tokens y máscara de atención, Transformer, promedio de tokens, normalización, similitud coseno y ordenamiento de vecinos por distancia. Define la política `verbatim-v1` con una comparación de entrada y salida y distingue configuración del modelo de sus operaciones internas. Comprueba que el cálculo manual coincide con el adaptador de VOC y muestra cómo conservar registros al reutilizar textos iguales.

Usa el kernel del Operador y la revisión de MiniLM ya descargada bajo `data/models/sentence-transformers`. Ejecuta las celdas en orden. La inferencia es local y pequeña. No descarga archivos, entrena modelos, modifica la caché de producción, consulta ZenML ni vuelve a producir los embeddings del corpus.

La tabla final es evidencia histórica de la ejecución del 3 de noviembre de 2026. Está identificada como transcripción, no como una comprobación nueva del paquete. Las similitudes del ejemplo didáctico no son métricas de calidad del corpus.

Ejecuta y guarda la libreta para conservar las salidas de esta versión. Las comprobaciones cubren formas, normalización, coincidencia del pooling manual, reconstrucción por texto y orden de vecinos. Los resultados pueden variar ligeramente con el entorno numérico.


## Figuras para el artículo

La sección final exporta `vecinos_coseno` (cuatro candidatos ordenados por distancia a una frase inventada) y `similitud_ejemplos` (matriz entre seis frases), en PNG y PDF. Incluye `vecinos_coseno.csv`, `frases_ejemplo.csv`, `vectores_ejemplo.npy`, `configuracion.json` y un manifiesto con sus hashes. Con `EXPORT_RESULTS = True`, crea una carpeta nueva en `data/reports/embeddings/<ejecución>/` e imprime la ruta.

La figura de vecinos sustituye al anterior diagrama `del_texto_al_embedding` para el artículo. Las exportaciones históricas se conservan. Esta escritura no modifica el paquete de embeddings del corpus.
