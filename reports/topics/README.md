# Cómo BERTopic convierte reseñas en tópicos

Abre `modelado-de-topicos-base.ipynb` desde `just jupyter-reports`, en `topics/`, con el kernel del Operador. La libreta utiliza la ejecución G0 `7ce4db4a-2890-4489-ac14-d1bf0a5ad35d` y verifica su manifiesto por SHA-256.

El recorrido explica las fases, antes de explorar sus resultados:

1. Leer y verificar los embeddings de entrada, con una fila por registro.
2. Entender UMAP, configurar el reductor y comparar vecinos en 384 y cinco dimensiones.
3. Entender densidad y alcanzabilidad mutua con puntos inventados, configurar HDBSCAN y mostrar sus asignaciones reales sobre las coordenadas guardadas.
4. Reunir textos por grupo y observar cómo CountVectorizer produce palabras, bigramas y conteos.
5. Calcular c-TF-IDF y comprobar sus pesos contra la biblioteca y el artefacto de G0.
6. Mostrar el ensamblaje de los componentes en BERTopic y revisar palabras y comentarios.

Ejecutar todas las celdas no vuelve a ajustar UMAP, HDBSCAN ni BERTopic y no genera embeddings ni llamadas a un LLM. Sí reconstruye el vocabulario y c-TF-IDF sobre los mismos grupos, además de calcular distancias ilustrativas. Los objetos muestran su configuración y las llamadas que iniciarían un nuevo ajuste permanecen comentadas.

Usa las dependencias ML y de visualización existentes del Operador. Conserva `data/topics/runs/<run_id>/release/`, `run.json` y los archivos de `work/`. La función `read_run_input` verifica los textos, su orden y la huella de los vectores, y la libreta contrasta esa entrada con el manifiesto del release. El lector de tópicos del producto sigue necesitando únicamente el release.

## Componentes compartidos con los CLI

La libreta invoca funciones Python del proyecto, sin ejecutar comandos de terminal. `read_topics` es el lector de `voc topics read`. `read_run_input` también valida la entrada antes de exportar el resultado del productor. Las funciones `build_reducer`, `build_clusterer`, `build_vectorizer`, `build_ctfidf` y `build_topic_model`, de `voc_ml.topics.models`, construyen los mismos componentes utilizados por `discover` y `voc-ml topics workflow`.

Construir esos objetos no ajusta modelos. Los parámetros siguen visibles mediante `get_params()`. Los cálculos manuales de vecindad, alcanzabilidad mutua y c-TF-IDF permanecen en la libreta para explicar el mecanismo y contrastarlo con la biblioteca.

## Figuras y evidencia

La sección final exporta PNG/PDF y CSV a una carpeta nueva en `data/reports/topics/`, con procedencia y hashes. Además de cobertura y tamaños, incluye un ejemplo de densidad con puntos inventados, dos coordenadas del ajuste de cinco dimensiones y los pesos c-TF-IDF reconstruidos. Los gráficos distinguen ejemplos didácticos de resultados reales. No se genera una reducción adicional para visualización.

Los CSV privados contienen textos e IDs para revisión y permanecen bajo `data/`. Revisa o limpia las salidas de la libreta antes de versionarla en un repositorio público. La revisión humana de ejemplos sigue pendiente y los conteos no constituyen una evaluación de calidad.

La copia ejecutada anterior se conservó en `data/reports/topics/notebook-backups/` antes de reorganizar las celdas y antes de compartir las funciones con producción. La libreta revisada se entrega sin salidas antiguas para que puedas ejecutarla en orden.
