# Entorno local de difusión

Este checkout usa Python 3.12 y el lock del Operador. Su entorno, modelos, entradas y seguimiento viven aquí. El demo original en `voc`, rama `voc-dev`, se conserva como referencia.

## Usar el entorno preparado

Desde la raíz de este repositorio:

```bash
bash scripts/difusion.sh python --version
bash scripts/difusion.sh voc-ml storage-info
bash scripts/difusion.sh voc --help
bash scripts/difusion.sh voc-ml --help
```

Para abrir una sesión con estas variables:

```bash
bash scripts/difusion.sh bash --noprofile --norc
```

El lanzador selecciona `.venv`, limpia las variables heredadas `VOC_*`, `DEMO_*`, `ZENML_*` y `MLFLOW_*`, fija las rutas de esta copia y activa el modo offline de Hugging Face. No carga `.env`. Activar solo `.venv` no sustituye al lanzador: dejaría sin fijar el almacenamiento.

## Token de Hugging Face entre sesiones de Argo

El lanzador fija `HF_TOKEN_PATH` a `$HOME/.cache/voc-difusion/hf-token`, fuera del repositorio. En Argo esa carpeta pertenece al volumen persistente `argo-v2_cache`. El `just down` de Argo ejecuta Compose sin eliminar volúmenes: después de `just up`, el archivo permanece disponible. No es necesario volver a transferirlo en cada sesión. No se verificó un apagado real durante esta preparación; se comprobó el montaje activo y el comando de apagado.

El archivo tiene permisos `600`. El lanzador elimina tokens heredados en variables de entorno y desactiva el envío implícito del token. Las operaciones autenticadas deben solicitarlo explícitamente (`token=True`); los modelos base siguen usando `token=False`. El modo offline continúa activo por defecto.

Comprobar disponibilidad local sin mostrar el secreto ni acceder a la red:

```bash
bash scripts/difusion.sh python scripts/check_hf_access.py
```

Comprobar acceso a ambas versiones privadas, sin descargar pesos:

```bash
bash scripts/difusion.sh env HF_HUB_OFFLINE=0 python scripts/check_hf_access.py --remote
```

La fuente de la credencial es `op://VAULT/ITEM/FIELD`. La copia local no se actualiza automáticamente si se rota o revoca el token en 1Password. Para renovarla, desde el Mac, sustituir `ARGO_CONTAINER_ID` por el ID actual mostrado por `docker ps` y ejecutar:

```bash
op read 'op://VAULT/ITEM/FIELD' | docker exec -i ARGO_CONTAINER_ID sh -c 'umask 077; mkdir -p /home/node/.cache/voc-difusion; tmp=$(mktemp /home/node/.cache/voc-difusion/hf-token.XXXXXX); cat > "$tmp"; if test -s "$tmp"; then chmod 600 "$tmp"; mv "$tmp" /home/node/.cache/voc-difusion/hf-token; else rm -f "$tmp"; exit 1; fi'
```

Esta transferencia no muestra el valor ni lo escribe en el historial de comandos. Conservar el volumen de caché: borrarlo elimina esta copia de la credencial. En ese caso se repite la transferencia desde 1Password.

## Reconstruir y verificar

Instalar las versiones fijadas, sin modificar el lock:

```bash
UV_PROJECT_ENVIRONMENT="$PWD/.venv" uv sync --project docker/operator --locked --python 3.12 --link-mode copy
```

La preparación del 4 de noviembre de 2026 pudo realizarse con `--offline`, usando la caché disponible. En otro equipo puede ser necesaria la descarga de dependencias. Los pesos de modelos no se descargan mediante este comando.

La copia selectiva ya fue realizada. Para reconstruirla con el demo disponible en su ruta actual:

```bash
bash scripts/difusion.sh python scripts/prepare_difusion_data.py --source /Users/ness/workspace/voc
bash scripts/difusion.sh voc-ml setup
bash scripts/difusion.sh python scripts/check_difusion_workspace.py --load-models
```

La copia rechaza archivos de destino diferentes en lugar de reemplazarlos. Verifica hashes y crea archivos independientes; los enlaces internos de los modelos resuelven exclusivamente dentro de la copia. No copia credenciales, bases de sesiones ni el seguimiento del demo.

La verificación compara los archivos con el recibo de copia, el contenido del corpus con su manifiesto, la alineación y normalización de todos los vectores, y las rutas efectivas de ZenML y MLflow. `--load-models` carga los dos modelos base en CPU, sin entrenar ni predecir. Sin esa opción verifica los archivos y las entradas sin cargar los pesos.

## Almacenamiento disponible

| Ruta dentro de `data/` | Contenido |
| --- | --- |
| `inputs/corpus/` | Corpus preparado histórico: 80.079 registros, Parquet y manifiestos |
| `inputs/embeddings/` | Release portable completo: 80.079 × 384, float32 |
| `prototypeV0/embeddings/` | Artefactos guardados de la muestra histórica de 1.000 reseñas del demo |
| `models/sentence-transformers/` | MiniLM multilingüe, revisión `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |
| `models/sentiment/` | Robertuito original, revisión `a2cc0f67ebd705c55191e25a05ba23d885fcc09b` |
| `zenml-client/`, `zenml-artifacts/` | Configuración, base local y destino de artefactos propios de ZenML |
| `mlflow/mlflow.db`, `mlflow/artifacts/` | Seguimiento SQLite y artefactos locales de MLflow |
| `setup/copy-receipt.json` | Procedencia, hashes y tamaños de la copia selectiva |
| `setup/workspace-check.json` | Resultado de la última comprobación del entorno |

Se copiaron 1.094.873.044 bytes, contabilizados en 61 entradas de archivos/enlaces. `data/` y `.venv/` están ignorados por Git. Los manifiestos mantienen la procedencia histórica S3; eso no implica una consulta a S3 al leer estas copias.

ZenML usa el stack `voc-local` y una base SQLite bajo `data/zenml-client`. MLflow usa SQLite bajo `data/mlflow`. No se levantaron servidores web ni se ocuparon puertos. No ejecutar los comandos generales de arranque/parada del demo sin preparar antes servicios y puertos propios.

## Alcance de esta preparación

La lectura portable de embeddings funciona mediante `voc.embeddings.validation.load_release`. Copiar el corpus no lo registra automáticamente en el catálogo personal de ZenML. Algunos comandos heredados de análisis todavía exigen entradas S3: la integración siguiente deberá permitirles consumir estas entradas locales verificadas.

Los checkpoints adaptados de Robertuito y el filtro de relevancia todavía no están integrados. Las 1.000 filas históricas se conservan como antecedente; no constituyen por defecto la muestra de la nueva comparación. Esta preparación no ejecutó ajustes, predicciones del corpus ni solicitudes a proveedores LLM.

Validación inicial: 77 pruebas de almacenamiento/configuración aprobadas, chequeo de sintaxis Bash y Ruff, carga offline de ambos modelos y comprobación de que las variables de almacenamiento de otra instalación no se heredan.
