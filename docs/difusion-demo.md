# Demostrar los análisis guardados

La interfaz original de `apps/voc-app` consulta copias locales verificadas de los dos análisis de julio–agosto de 2026. El recorrido no recolecta datos, entrena modelos, recalcula sentimiento ni llama a un LLM. Ejecutar desde la raíz de **voc-difusion**; no se utiliza el catálogo ni el estado del demo en `voc`.

## Preparación

Los entornos y paquetes ya están preparados en este workspace. Para reconstruir el entorno de la app desde los locks existentes:

```sh
UV_PROJECT_ENVIRONMENT="$PWD/data/venvs/app" uv sync --project apps/voc-app/server --locked --group dev
npm --prefix apps/voc-app/web ci
npm --prefix apps/voc-app/web run build
bash scripts/difusion-demo.sh prepare
```

`prepare` lee el protocolo de `data/experiments/julaug-2026`, verifica los artefactos locales y construye dos publicaciones. Si ya existen, las valida y reutiliza. Instala ambas en `data/difusion-app/catalog` y deja activa la alternativa. El estado de la aplicación vive en `data/difusion-app/state`; los paquetes de origen, en `data/difusion-app/bundles`. No ejecutar productores para repetir esta demostración.

## Arranque, selección y parada

```sh
bash scripts/difusion-demo.sh serve
```

El servidor permanece en primer plano. En otra terminal:

```sh
bash scripts/difusion-demo.sh select base
bash scripts/difusion-demo.sh status
```

Abrir `http://localhost:8092/dashboard/`. Para cambiar al otro análisis:

```sh
bash scripts/difusion-demo.sh select alternative
```

Recargar el navegador después de seleccionar: una página ya abierta conserva la publicación que cargó. `status` muestra sistema, ID del análisis e ID de la publicación. El selector cambia un puntero local; no modifica los resultados.

**Parada:** `Ctrl+C` en la terminal de `serve`. Volver a ejecutar `serve` conserva la selección y los datos. Apagar el contenedor detiene el proceso; los entornos y datos permanecen en el directorio persistente del clon.

### Acceso desde el Mac

8092 es el puerto **dentro del contenedor Argo**. La configuración inspeccionada solo publica 2718 y 9119 al host; por ello, el enlace anterior todavía requiere un reenvío de puerto. La prueba automática sí accede a 8092 desde dentro de Argo.

Para acceso habitual, agregar al servicio `workspace` del Compose **de Argo**, desde el host, la entrada `"127.0.0.1:8092:8092"` bajo `ports` y recrear ese contenedor con su procedimiento habitual. Guardar las libretas antes de recrearlo. Después iniciar de nuevo `serve`. No modificar el Compose de `voc`, ni detener sus servicios. Como alternativa, el reenvío de puertos del editor hacia 8092 permite probarlo sin recreación. No cambiar el puerto de Jupyter ni el de Hermes.

## Recorrido y cifras de referencia

1. Leer el ID de publicación y contrastarlo con `status`.
2. Ver los indicadores y la distribución de sentimiento.
3. Abrir el tema 0 y la pestaña **Reseñas**. Los textos se conservan completos; el panel se desplaza y permite pasar de página.
4. Filtrar por **Negativas**, restablecer y probar **Google Play**.
5. Cambiar de sistema por CLI y recargar para repetir.

| Sistema | Reseñas publicadas | Tópicos | Sin tópico | Negativas / neutras / positivas |
|---|---:|---:|---:|---|
| Base | 1.000 | 18 | 47 | 476 / 237 / 287 |
| Alternativa | 987 | 28 | 141 | 409 / 47 / 531 |

Los **13 retirados por relevancia** (2 de X y 11 de YouTube) se contabilizan en `data/experiments/julaug-2026/relevance.csv` y `report/retention.csv`. La comprobación automática verifica que esos IDs son exactamente la diferencia entre las publicaciones. Permanecen consultables en la base. La revisión humana registró diez exclusiones correctas y tres relevantes perdidos; no es una evaluación de todos los registros retenidos.

“Sin tópico” significa HDBSCAN `-1`; no equivale a exclusión por relevancia. Los paneles desplegables de procedencia describen el corpus histórico completo de 80.079 registros; los indicadores de análisis describen la selección de 1.000 o 987.

Los nombres de temas proceden de las palabras clave. No hay interpretaciones LLM nuevas, y el panel de “funcionalidad” puede quedar vacío por ello. El enlace de conversación pertenece a la interfaz original, pero este arranque usa `VOC_PLANNER_MODE=fixture`: **la conversación real con LLM queda fuera de esta demostración**. El modo de acceso es local; no representa un despliegue con autenticación de producción.

## Repetir las comprobaciones

Para comprobar también arranque, parada y reinicio, con 8092 libre:

```sh
bash scripts/check_difusion_demo.sh
```

Este comando deja el servidor detenido y la alternativa seleccionada. Para comprobar solo las consultas, con el servidor ya encendido, ejecutar secuencialmente:

```sh
bash scripts/difusion.sh python scripts/check_difusion_demo.py
node apps/voc-app/web/scripts/check-difusion-demo.mjs
```

La primera prueba compara todos los textos, asignaciones y etiquetas publicados con los candidatos guardados. La segunda usa Chromium y la API real, sin respuestas simuladas: verifica indicadores, tema, textos completos, paginación y filtros. Ambas seleccionan base y alternativa durante la prueba y dejan activa la alternativa. No ejecutarlas simultáneamente ni mientras otra persona esté demostrando el panel.

Chromium debe estar instalado con Playwright. En este contenedor sus bibliotecas Debian y fuentes se extrajeron en `data/browser-libs`, sin cambiar paquetes del sistema. El verificador utiliza esa ruta si existe; sus archivos persisten junto al clon. Para reconstruir esa preparación en Debian 12, usar `bash scripts/prepare_difusion_browser.sh`. El navegador en sí puede instalarse con `npm --prefix apps/voc-app/web exec -- playwright install chromium`.

Evidencia local: `data/reports/difusion/demo/` contiene `api-verification.json`, `browser-verification.json`, `lifecycle-verification.json` y capturas de ambos paneles y detalles de reseñas. Son comprobaciones de funcionamiento y fidelidad de lectura; no prueban que un sistema sea más preciso. Los archivos se regeneran al repetir la comprobación y no se añaden a Git.
