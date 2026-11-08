# Libreta de apoyo al artículo · Equipo 14

Abrir [evidencia-articulo-14.ipynb](../difusion/evidencia-articulo-14.ipynb) en Jupyter, o consultar [la lectura HTML con salidas](evidencia-articulo-14.html).

La libreta reúne la cobertura del corpus, muestra, configuración, evaluación de relevancia, evaluación histórica de Robertuito, exclusiones, agrupamiento, interpretación, sentimiento, costos y las figuras documentales del artículo. Incluye las once figuras (nueve en el cuerpo y dos anexos), las seis tablas numeradas y los dos ejemplos de la sección 3.6.

## Qué reproduce

- Recalcula estadísticas y gráficas a partir de predicciones y asignaciones guardadas.
- Contrasta cada nombre, tamaño y conteo de sentimiento del catálogo de 46 tópicos.
- Comprueba la composición de la muestra, los 13 comentarios retirados y su revisión registrada.
- Recalcula métricas de relevancia desde las 160 predicciones OOF y las del Robertuito adaptado desde su matriz histórica de confusión.
- Reproduce los escenarios de costos desde importes por llamada, sin exponer nombres de claves ni cuentas.
- Conserva diagramas y capturas como recursos documentales, no como resultados experimentales.

No vuelve a entrenar ni ejecuta inferencia. No requiere credenciales, modelos descargados, la carpeta `data/`, ZenML, AWS ni conexión a Internet durante la ejecución. Reproducir métricas no valida la interpretación semántica ni prueba generalización o causalidad.

## Abrir y ejecutar

En el workspace configurado, ejecutar `just difusion-jupyter` y abrir `difusion/evidencia-articulo-14.ipynb`. Seleccionar **Run → Run All Cells**.

Para una instalación independiente, usar Python 3.12 e instalar las versiones de `requirements-evidencia-articulo-14.txt`. Conservar la estructura `reports/difusion/evidencia-articulo-14.ipynb` y `reports/soporte-articulo-14/sources/evidencia-articulo-14/` completa. La instalación de dependencias puede requerir Internet, la ejecución de la libreta no.

La libreta escribe nuevos resultados en `exports/evidencia-articulo-14/<fecha>/`. `verificacion.json` registra las comprobaciones y hashes. Las entradas nunca se sobrescriben. `verificacion-portabilidad.json` documenta la ejecución en una carpeta temporal sin el resto del repositorio y con las credenciales retiradas del entorno del kernel.

## Procedencia y cita

La procedencia se conserva en `sources/evidencia-articulo-14/manifest.json` y `fuentes-publicas.json`. La evaluación histórica incluye revisión inmutable del checkpoint y una copia de sus métricas. La fuente del criterio histórico F1 aparece en el extracto de la página 50 de la entrega anterior.

Cita propuesta: Equipo 14. (2026). *Evidencia experimental de Voz del Cliente más allá del NPS* [Cuaderno Jupyter y archivos de apoyo]. GitHub.

Enlace previsto al versionar estos archivos: https://github.com/nessBautista/voc/blob/voc-difusion/reports/difusion/evidencia-articulo-14.ipynb

Antes de compartir el artículo, publicar juntos libreta, HTML, archivos de apoyo, guía y dependencias. El enlace remoto depende de ese push. Las libretas originales se conservan como respaldo histórico.

Los borradores previos están en `../drafts/difusion/`. La libreta principal conserva su enlace de referencia. Para verificarla aisladamente, ejecutar desde la raíz del repositorio `python reports/soporte-articulo-14/verificar_evidencia_articulo14.py`.
