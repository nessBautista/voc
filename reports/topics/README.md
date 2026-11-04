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

## Comparación de configuraciones

`comparacion-de-configuraciones.ipynb` es la libreta de resultados del estudio G/R. Tiene fijado el estudio `066c6cfc-49ef-40bc-b548-c3eee3dfeced`, cuyo protocolo conserva la muestra de G0. Lee cada release con su hash y muestra también los intentos fallidos o pendientes. La libreta didáctica anterior permanece independiente.

Los ajustes se ejecutan con `voc-ml topics study`, uno por comando. `create` fija las entradas, `run` consume un intento, `inspect` lee resultados, `preselect` congela hasta dos variantes para las semillas 13 y 73 y `select-parent` fija el agrupamiento de R. Usa `--help` en cada subcomando. No repitas un intento fallido ni crees otro estudio para eludir el presupuesto.

G1 cambia UMAP a cuatro dimensiones, G2 cambia el tamaño mínimo de HDBSCAN a 20 y G3 usa `leaf`. R0 reutiliza c-TF-IDF, R1 usa KeyBERTInspired y R2 añade MMR. Los refinamientos generan releases autocontenidos, con identidad propia y manifiesto padre, conservando byte a byte selección, asignaciones y coordenadas. Su lectura no necesita el modelo ni el directorio del padre. La producción sí requiere los archivos de trabajo verificados y pesos locales.

La sección de revisión incluye una variable `REVIEW_ENTRY` para mostrar los ejemplos de cada variante. La autoría, puntuaciones y notas comienzan vacías. La preselección requiere una revisión explícita y un motivo, no se calcula un ganador automático por cobertura.

Al final se exportan CSV, PNG/PDF, plantillas de revisión y un manifiesto de procedencia a una carpeta nueva en `data/reports/topics/studies/<study_id>/`. No se ejecutan modelos al regenerar estas figuras. Las plantillas contienen reseñas privadas y deben permanecer fuera de Git. La publicación de figuras en el artículo requiere revisar sus textos y dejar claro que son resultados de una muestra.


## Resultados del piloto de 5.000 reseñas

Abre `resultados-del-piloto.ipynb` en `topics/` desde `just jupyter-reports`. Lee el protocolo `55822245-4bb3-4f97-89ac-58d536bb2fed` y los tres releases fijados por hash. Reutiliza `inspect_pilot`, `read_topics`, `compare_assignments` y `review_sample`, sin ajustar modelos ni cargar pesos.

La libreta compara cobertura, tamaños y recursos. Presenta G3/c-TF-IDF y R2 sobre los mismos ejemplos, con textos completos. También permite revisar G0 y cinco casos sin asignación. La revisión exploratoria usa la pauta del incremento y no asigna puntuaciones automáticamente.

La exportación final guarda tablas, figuras y un manifiesto en `data/reports/topics/corpus/<study_id>/<ejecución>/`. Las notas completas pueden guardarse en `data/reports/topics/corpus/pilot-review.json`. No cambian el protocolo experimental ni habilitan el corpus completo. Las salidas de revisión contienen reseñas privadas, por lo que deben limpiarse antes de publicar la libreta en GitHub.


## Procesamiento completo

El recorrido completo se inicia de forma explícita con `voc-ml topics corpus full`. `resources` consulta el presupuesto, `create` verifica el piloto y congela la selección completa, `run G0`, `run G3` y `run R2` ejecutan una operación por comando y `inspect` verifica lo guardado. Revisa cada resultado antes de iniciar el siguiente. Los fallos consumen intentos y no se repiten automáticamente.

El estudio completo se guarda en `data/topics/corpus/full-v1/full.json`, con referencias al piloto y presupuestos por operación. R2 deriva del nuevo G3 completo y conserva sus pertenencias. La libreta del piloto permanece como evidencia de su propia población. Abre `resultados-del-corpus-completo.ipynb` para revisar los tres releases completos, fijados por identidad y hash. No ejecuta modelos. Exporta las figuras, tablas, ejemplos privados y procedencia a `data/reports/topics/corpus/<study_id>/<ejecución>/`. La pauta de revisión conserva los textos completos. El gráfico de tamaños usa escala logarítmica y la comparación advierte que las coordenadas UMAP de G0 y G3 no son idénticas. Las observaciones pueden guardarse en `data/reports/topics/corpus/full-review.json`.

## Interpretación con el mecanismo de voc-dev

El flujo vigente usa **G3 + R2** y el prompt fijo `voc-topic-es-v3`. La referencia es `voc-dev`, commit `29b6a25497a80d5f0691e5654ee8fcd802885e2f`. Ya no se eligen variantes P1/P2 desde el CLI.

`config/topics/interpretation.toml` replica la configuración del prototipo: Gemini 2.5 Flash, seis reseñas por tópico, 900 caracteres por reseña, temperatura 0 y 800 tokens de salida. Se toman primero los IDs representativos y después los miembros en el orden almacenado; se eliminan IDs repetidos, pero se conservan textos iguales de distintas reseñas. El primer recorrido permite hasta 20 tópicos explícitos. No interpreta automáticamente los 2.199 tópicos.

`prototype.prepare` verifica los releases y prepara solicitudes sin red. `prototype.build_request` conserva el prompt, el payload, el esquema y el orden de sus campos. El campo histórico `dataset_release_id` contiene aquí el ID del artefacto preparado. El payload incluye IDs de reseña, como el prototipo; el modelo cita etiquetas E1, E2, etc. El SDK de OpenAI llama a OpenRouter mediante `chat.completions.create`, con JSON Schema estricto, razonamiento desactivado, timeout de 60 segundos y cero reintentos automáticos.

Se mantienen los controles actuales de presupuesto, proveedor Google AI Studio, tarifa máxima y `data_collection: deny`. Son controles adicionales al prototipo; no se afirma identidad completa de enrutamiento. La validación local sigue comprobando campos, longitudes y citas. Una salida estructurada no garantiza terminar antes de 800 tokens ni demuestra fidelidad semántica.

### Prueba en el Operador

Dentro de `just shell`, desde `/workspace`, prepara dos casos conocidos: 975 tuvo un fallo por longitud; 59 ofreció una descripción útil de notificaciones. Son comprobaciones de integración, no una evaluación independiente.

```sh
voc-ml topics interpretation plan --topic-id 975 --topic-id 59
```

Este comando verifica las entradas y no escribe ni consulta al proveedor. Después congela la ejecución:

```sh
voc-ml topics interpretation create --topic-id 975 --topic-id 59
```

`create` consulta el catálogo público y crea `data/topics/interpretation/prototype-execution/`, sin solicitudes al modelo. Rechaza sobrescribir una ejecución existente. Comprueba el primer caso:

```sh
voc-ml topics interpretation run 975
```

La ejecución usa ZenML y MLflow. Revisa el resultado antes de continuar con `run 59`. `inspect` verifica recibos, costos e intentos pendientes sin red. Si vuelve a fallar, conserva el intento y revisa su causa; no borres registros ni aumentes límites silenciosamente. Un fallo por longitud con costo y tracking conocidos puede conservarse mediante `review-failure 975`, sin reenviarlo. Los errores de transporte con consumo incierto requieren inspección.

La homologación modifica conjuntamente transporte, orden del esquema y selección de evidencia. Su éxito no demostraría que uno de estos cambios, por sí solo, resuelve la repetición. La ejecución real aún debe comprobarlo.

### Libretas y evidencia histórica

`interpretacion-de-topicos.ipynb` utiliza las funciones del flujo fijo y ejemplos sintéticos. Prepara la solicitud y contrasta validez estructural con fidelidad semántica sin invocar modelos. Exporta a `data/reports/topics/interpretation-didactica/`.

`resultados-de-interpretacion.ipynb` conserva el desarrollo P0/P1/P2 y la reserva con sus recibos, fallos y revisión asistida. Al final lee por separado la comprobación homologada, cuando exista, y exporta sus resultados bajo `data/reports/topics/interpretation/prototype/`. No inicia pipelines. Las copias ejecutadas previas se conservan en `data/reports/topics/notebook-backups/`.

Los registros anteriores permanecen en `data/topics/interpretation/execution/` y `reserve-execution/`. Consulta `voc-ml topics interpretation history development` o `history reserve`. Sus módulos de análisis y configuraciones se conservan para reproducir la lectura histórica, pero no forman parte de un nuevo recorrido de búsqueda de prompts. No se sustituyen los fallos antiguos por respuestas nuevas ni se mezclan sus denominadores.

El lector portable `voc.topics.interpretation_results.read_interpretation` verifica tanto los recibos históricos v1 como los nuevos v2. La capa `voc` sigue sin depender del SDK, ZenML o `voc_ml`. El adaptador de proveedor está en `voc_ml`; el CLI y el paso de ZenML delegan en las operaciones.
