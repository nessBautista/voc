# Explorar c-TF-IDF con marimo

La libreta [explorar-ctfidf.py](explorar-ctfidf.py) acompaña la sección 3.6 del artículo. Usa tres cajas de comentarios **inventados para enseñar el cálculo**; no reemplaza los resultados del corpus.

Puedes consultar [la copia HTML ejecutada](explorar-ctfidf.html) sin iniciar Python. Esa copia muestra la configuración inicial; para recalcular con los controles necesitas abrir la libreta en marimo.

## Abrir la versión interactiva

Desde la raíz de `voc-difusion`, en el contenedor con el entorno Python del proyecto:

```bash
just difusion-marimo
```

En el explorador abre `difusion/explorar-ctfidf.py`. Este comando conserva la autenticación de marimo. Usa el enlace con token que imprime la terminal; no compartas ese token. El puerto 2718 debe estar publicado por el contenedor para abrirlo desde el host. Si ese puerto ya tiene un servidor activo, ciérralo primero o usa otro puerto publicado. No hace falta una clave de OpenRouter ni descargar modelos.

Si Python y las dependencias están instalados directamente en tu equipo, basta con:

```bash
marimo edit reports/drafts/difusion/explorar-ctfidf.py
```

## Recorrido sugerido

1. Lee las tres cajas. Mantén la configuración inicial: unigramas, lista didáctica de exclusiones y fórmula básica.
2. En la matriz busca **transferencia** y sigue su cálculo en A: conteo, longitud del grupo, TF, frecuencia global, IDF y multiplicación final.
3. Observa las palabras con más peso en cada caja. Cambia el grupo y el término del selector.
4. Activa la raíz cuadrada y compara ambas columnas de pesos. Esta es la variante usada por el proyecto.
5. Prueba los tres experimentos guiados: añadir «transferencia» a B, duplicar A y mezclar una expresión de elogio con una transferencia.
6. Descarga el JSON del experimento o el CSV de palabras si quieres conservar un hallazgo.

Cada cambio recalcula el ejemplo y comprueba los pesos contra `ClassTfidfTransformer` de BERTopic. Puedes desplegar las celdas para ver y editar el código. Los controles no modifican el corpus ni sus asignaciones.

## Diferencias respecto al experimento del artículo

- Las cajas ya vienen asignadas manualmente; aquí no se ejecuta UMAP ni HDBSCAN.
- La lista de exclusiones es deliberadamente pequeña y no reproduce la del proyecto.
- La configuración inicial usa unigramas y la fórmula básica para facilitar la intuición. El proyecto usa unigramas/bigramas y `reduce_frequent_words=True`.
- La libreta [ejemplo-ctfidf.ipynb](ejemplo-ctfidf.ipynb) es el respaldo con conteos reales de las 1.000 reseñas del modelo base. Incluye el documento agregado de los casos sin tópico (`-1`).
- Ni los pesos léxicos ni su cambio al editar textos demuestran mejor calidad de un tópico.

## Verificación y exportación

Probado con marimo 0.25.0 y BERTopic 0.17.4. La libreta verifica ambas fórmulas con tolerancia `rtol=atol=1e-12`. Se comprobaron los controles de duplicación, frecuencia global, raíz cuadrada, bigramas y entrada vacía en una sesión real. Evidencia: [verificacion-explorar-ctfidf.json](exports/verificacion-explorar-ctfidf.json).

Para regenerar la copia de lectura, desde la raíz del repositorio:

```bash
bash scripts/difusion.sh marimo export html reports/drafts/difusion/explorar-ctfidf.py -o reports/drafts/difusion/explorar-ctfidf.html --force
```

Fuentes: [explicación de BERTopic](https://maartengr.github.io/BERTopic/getting_started/ctfidf/ctfidf.html) e [implementación del transformador](https://maartengr.github.io/BERTopic/api/ctfidf.html).
