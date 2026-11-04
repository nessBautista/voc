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

La lectura portable de embeddings funciona mediante `voc.embeddings.validation.load_release`. Copiar el corpus no lo registra automáticamente en el catálogo personal de ZenML. Los comandos `voc-ml analysis run` y `voc-ml sentiment workflow` aceptan ahora `--source local --local-inputs config/analysis/difusion-local-inputs.toml`. Ese TOML fija rutas relativas y hashes de manifiestos; cada ejecución vuelve a verificar archivos y alineación. Se conserva la identidad histórica de publicación como procedencia, sin consultar S3.

```bash
bash scripts/difusion.sh voc-ml analysis preflight-local --local-inputs config/analysis/difusion-local-inputs.toml
```

El preflight no crea runs ni ejecuta modelos. `analysis run` sí ajusta tópicos y calcula sentimiento mediante el pipeline ZenML existente; `sentiment workflow` calcula solo sentimiento. Sus opciones `--limit` sirven para pruebas técnicas pequeñas, no sustituyen al diseño de la muestra experimental. Todavía no ejecutar la comparación completa con la selección predeterminada de primeras filas.

Los checkpoints adaptados de Robertuito y el filtro de relevancia todavía no están integrados. Las 1.000 filas históricas se conservan como antecedente; no constituyen por defecto la muestra de la nueva comparación. Esta preparación no ejecutó ajustes, predicciones del corpus ni solicitudes a proveedores LLM.

Validación inicial: 77 pruebas de almacenamiento/configuración aprobadas, chequeo de sintaxis Bash y Ruff, carga offline de ambos modelos y comprobación de que las variables de almacenamiento de otra instalación no se heredan.

## Comparación integrada por CLI

Actualización: el filtro SVM y Robertuito tiendas v3 ya están integrados. La
alternativa usa tiendas v3 también en X/YouTube por decisión explícita del equipo;
la evaluación publicada del checkpoint corresponde a tiendas, no valida este traslado.

`voc-ml experiment prepare` fija primero los IDs comunes, estratificados por
plataforma y mes, excluyendo IDs anotados para relevancia y textos exactamente
iguales. Luego aplica el filtro solo a X/YouTube y conserva los vectores por ID.
No se conocen los IDs del entrenamiento de sentimiento: la comparación es
operativa y descriptiva, no un test independiente de exactitud.

```bash
bash scripts/difusion.sh voc-ml experiment prepare --local-inputs config/analysis/difusion-local-inputs.toml --annotations data/relevance/reconstruction/annotations-reviewed.csv --relevance-model data/relevance/model-v1/model.json --relevance-sha256 f8e24c1403a1103d4384863687d33308a343eef6064279a5bf5f612842b203d8 --output data/experiments/julaug-2026 --start 2026-07-01 --end 2026-09-01 --size 1000 --seed 42
bash scripts/difusion.sh voc-ml experiment run base --directory data/experiments/julaug-2026
bash scripts/difusion.sh voc-ml experiment run alternative --directory data/experiments/julaug-2026
```

Julio y agosto son los últimos dos meses completos de la copia histórica;
septiembre contiene una recolección parcial. Cada sistema ajusta BERTopic mediante
ZenML, calcula sentimiento y refina palabras clave con KeyBERTInspired + MMR.
Usan los mismos parámetros de agrupamiento (UMAP 5D; HDBSCAN eom, mínimo 10).
El protocolo fija hashes y el estado impide repetir ajustes inadvertidamente.
Hay progreso de sentimiento por lote y seguimiento local MLflow. No se solicitan
interpretaciones LLM en esta comparación. Los temas requieren revisión de su
contenido: contar grupos no demuestra que sean funcionalidades de la aplicación.
