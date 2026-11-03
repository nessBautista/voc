# EDA del corpus actualizado

Abre `estado-de-los-datos.ipynb` con el entorno del Operador. Analiza la versión local de 84.479 registros, sin recolectar datos, entrenar modelos ni llamar a un LLM.

## Abrir en Jupyter

Desde la raíz del checkout ejecuta `just jupyter-reports`. En Jupyter abre `eda/estado-de-los-datos.ipynb`. También puedes usar `just jupyter` y navegar a `reports/eda/estado-de-los-datos.ipynb`.

El lanzador existente expone `reports/` mediante `notebooks/reports`. No necesitas un enlace adicional.

Selecciona **Restart Kernel and Run All Cells**. No necesitas reconstruir el contenedor. Las dependencias del notebook ya pertenecen al entorno ML del Operador.

## Lectura y salidas

- `READER_MODE = "zenml"` utiliza el lector personal habitual, con el servidor y los artefactos del Operador.
- `READER_MODE = "parquet"` utiliza explícitamente el Parquet local inventariado, a través del mismo API personal y su adaptador de lectura. No omite la validación de identidad o contenido. Esta alternativa permite comprobar los cálculos sin un servidor ZenML.
- `EXPORT_RESULTS = True` guarda tablas CSV, tres figuras en PNG y PDF, y un manifiesto en `data/reports/eda/<artifact_id>/<ejecución>/`, fuera de Git. Cada ejecución crea su propia carpeta.

La ejecución guardada se conserva durante la revisión. Antes de versionar el notebook, revisa sus salidas o límpialas. Conserva el notebook histórico `notebooks/Avance1.14Equipo.ipynb` sin cambios. La identidad del corpus, las reglas de preparación, las limitaciones de las fechas y la procedencia de las tablas se explican dentro del nuevo notebook.

Las exportaciones anteriores a la reubicación permanecen en `data/reportes/eda/`. Las ejecuciones nuevas utilizan `data/reports/eda/`. Los manifiestos y resultados anteriores se conservan sin cambios.


## Figuras para el artículo

La libreta genera `estado_del_corpus` (composición y disponibilidad de metadatos), `cobertura_mensual` (cuatro fuentes con eje temporal común) y `longitudes` (apoyo para anexo). La exportación está al final y muestra la carpeta de la ejecución. Las nuevas figuras se calculan a partir del mismo corpus verificado, sin reutilizar cifras de otro demo.
