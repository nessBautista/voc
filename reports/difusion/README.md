# Comparación integrada

`comparacion-base-alternativa.ipynb` lee los artefactos verificados de
`data/experiments/julaug-2026/`, producidos mediante `voc-ml experiment`.
No ejecuta modelos ni solicita interpretaciones LLM.

Ejecutar desde el entorno de `scripts/difusion.sh`; la ubicación efectiva de datos
se obtiene de `voc.paths.data_root`. La sección final exporta el resumen JSON,
la figura, transiciones de sentimiento y todos los textos excluidos a
`data/experiments/julaug-2026/report/`. El resumen agregado está versionado en
`docs/evidence/diffusion-comparison-v1.json`.

Lectura: 1.000 registros iniciales, 987 retenidos. El SVM excluye los 11 de YouTube
y dos de X. La inspección asistida identifica menciones explícitas a funciones de
la app entre las exclusiones; requiere confirmación humana. Los 314 cambios de
sentimiento no son correcciones verificadas. No hay referencia independiente de
sentimiento ni evaluación humana nueva de la calidad de los tópicos.

Los tiempos son observaciones de estas ejecuciones, no un benchmark. Base usa
lotes de 16 y alternativa de 8. Tiendas v3 está configurado para todas las fuentes;
no quedaron registros de YouTube después del filtro en esta muestra concreta.
