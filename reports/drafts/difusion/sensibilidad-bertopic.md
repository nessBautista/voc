# Sensibilidad de BERTopic: base frente a alternativa

- [Libreta ejecutada](sensibilidad-bertopic.ipynb).
- [HTML de lectura](sensibilidad-bertopic.html), con tablas desplegables por configuración.

Abrir `just difusion-jupyter` y seleccionar `drafts/difusion/sensibilidad-bertopic.ipynb`. El HTML puede consultarse sin instalar ni ejecutar el proyecto.

## Orden sugerido para analizar resultados

1. **Secciones 1–9:** sensibilidad del agrupamiento sobre las 1.000 reseñas, manteniendo fijo el resto del procesamiento.
2. **Secciones 10–12:** las mismas diez configuraciones sobre 1.000 reseñas sin filtro y 987 con filtro. Incluye los 13 textos retirados y los tamaños de los grupos lado a lado.
3. **Sección 13:** cuatro condiciones B0/leaf × con/sin filtro. ARI sobre los mismos 987 identificadores ayuda a distinguir similitud de tamaños de similitud de pertenencia.
4. **Secciones 14–15:** nombres y descripciones del LLM, categorías, tamaños y sentimiento de todos los grupos. Abrir cada configuración y cada descripción en las tablas. La figura detallada usa H-leaf por defecto.
5. **Sección 16:** desacuerdos entre Robertuito original y adaptado y distribuciones sobre grupos fijos. Así se separa el cambio de clasificador del cambio de agrupamiento. También muestra la evidencia exacta enviada al LLM para un tópico seleccionado.
6. **Sección 17:** exportación, costos, estados de solicitudes y límites de interpretación.

## Operación y límites

Para consultar cachés, dejar `EJECUTAR_AJUSTES`, `EJECUTAR_ALTERNATIVA` y `EJECUTAR_LLM` en `False`. Las salidas de la libreta ya están guardadas. Ejecutar requiere las entradas locales y el entorno de `voc-difusion`, no basta con descargar únicamente el ipynb.

Cambiar `CONFIGURACION_PAREADA_A_REVISAR` permite dibujar otro par de sistemas, usando nombres ya guardados. `TOPICO_PARA_INSPECCIONAR` selecciona un grupo para consultar sus evidencias. Estos cambios no requieren nuevas llamadas si se ejecutan solamente las celdas de consulta.

Los nuevos ajustes requieren habilitar `EJECUTAR_ALTERNATIVA`. Nuevas interpretaciones requieren `EJECUTAR_LLM` y la credencial inyectada `OPENROUTER_API_KEY`. Nunca colocar la llave en la libreta. El mecanismo usa el mismo prompt, JSON estructurado y validación que el prototipo, con un máximo de USD 2 de presupuesto planificado, 500 intentos y cuatro solicitudes simultáneas. La estimación monetaria no es un límite impuesto por el proveedor. No hay reintentos automáticos y los costos desconocidos conservan su reserva.

Las predicciones de sentimiento se reutilizan por identificador y texto. El filtro no se entrena otra vez. Los modelos usados son Robertuito original para base y el adaptado de tiendas v3 para alternativa, en todas las fuentes. El sentimiento no interviene en UMAP ni en HDBSCAN.

## Evidencia

- Primera fase: `exports/sensibilidad-bertopic/latest.json`.
- Fase pareada: `exports/sensibilidad-bertopic-pareada/latest.json`.
- Cada exportación tiene su manifiesto con hashes y archivos independientes.
- La fase pareada incluye `catalogo-completo.csv`, `resumen.csv`, `efecto-filtro.csv`, `comparacion-leaf.csv`, `sentimiento-grupos-fijos.csv` y las solicitudes/respuestas LLM sin cabeceras ni credenciales.
- Runtime: `data/experiments/bertopic-sensibilidad-pareada/`. Una corrida incompleta se conserva para inspección y no se sobreescribe.

Las categorías del LLM no son una evaluación humana. El experimento permite comparar sensibilidad, cobertura y ejemplos, pero no establece cuál es el mejor sistema. La estabilidad entre semillas, revisión humana y evaluación en otra ventana temporal siguen pendientes. La libreta de referencia del artículo y el PDF no se modifican.
