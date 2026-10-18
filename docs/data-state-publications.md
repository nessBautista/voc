# Construir una publicación del estado de los datos

Construye un paquete local a partir de un dataset preparado existente. El resultado
contiene `publication.duckdb` y `manifest.json`: cantidades por fuente y mes,
fechas conocidas y estado declarado de embeddings. No contiene textos de reseñas
ni vectores. La adquisición y los modelos siguen siendo operaciones independientes.

## Antes de comenzar

Desde la raíz del repositorio en tu Mac, entra al contenedor Operador:

```sh
just shell
```

El Operador instala `voc[publications]`, incluido DuckDB. Dentro del contenedor,
`/workspace/data` corresponde a `data/` en tu checkout. Git y el contexto de
construcción Docker excluyen ese directorio, incluidos sus manifiestos JSON.
El código está montado desde el repositorio: este cambio no requiere reconstruir
la imagen si ya tienes instalado el extra `publications`.

## Seleccionar y construir

Consulta la identidad existente desde Workspace:

```sh
voc dataset-info --source s3
```

Para S3, copia el `release_id`; para `--source personal`, copia el `artifact_id`.
Selecciona una versión concreta: el constructor no acepta `latest`. Define
`DATASET_VERSION` con ese UUID y ejecuta, usando un destino que todavía no exista:

```sh
voc publication build \
  --source s3 \
  --dataset-version "$DATASET_VERSION" \
  --output /workspace/data/data-state-demo/real-a
```

Para un artefacto personal, cambia `--source` y utiliza su UUID. La lectura personal
usa el adaptador ZenML existente; la lectura S3 reutiliza su lector y caché.
Una caché incompleta puede necesitar una descarga autenticada. El comando no sube
el paquete a S3 ni ejecuta nuevamente adquisición o preparación.

La salida JSON indica `publication_id`, `dataset_id`, `schema_version`,
`destination` y `warnings`. Si `real-a` ya existe, selecciona otro nombre,
por ejemplo `real-b`; una publicación existente no se sobrescribe.

## Comprobar el paquete

Desde Workspace, sustituye la ruta si elegiste otro destino:

```sh
python - <<'PY'
from voc.publications.store import validate_bundle

manifest = validate_bundle("/workspace/data/data-state-demo/real-a")
print(manifest["publication_id"])
print(manifest["database"]["sha256"])
PY
```

Debes obtener su identificador y una huella SHA-256. El validador comprueba tamaño
y hash antes de abrir DuckDB, después revisa esquema, relaciones y cantidades,
y finalmente compara la identidad del manifiesto con la tabla de publicación.
La lectura no modifica el paquete. Los hashes detectan cambios; la confianza en
quien publica depende de los permisos de almacenamiento.

## Entender qué se conserva

- `dataset_id` es `s3-<release_id>` o `personal-<artifact_id>`. Reexportar la misma
  identidad conserva ese valor; cada paquete recibe un nuevo `publication_id`.
- `source_kind` y `source_version` conservan el tipo y UUID nativo de la fuente.
  Los fixtures usan identidades sintéticas y solo se construyen desde Python.
- Las filas sin plataforma se cuentan como `unknown`. Las fechas no utilizables
  permanecen en `unknown_dates`, fuera de los conteos mensuales. Las fechas de
  registros se normalizan a UTC siguiendo `voc.datasets.summary`.
- `created_at` del manifiesto es la creación del paquete. `prepared_at` solo se
  copia si el snapshot la declara explícitamente. La fecha de publicación S3 no
  se convierte en fecha de preparación o recolección.
- El adaptador actual no verifica colecciones ni artefactos de embeddings:
  publica recolección `unknown` y embeddings `not_reported`. Eso no significa que
  no existan embeddings. Sus adaptadores de evidencia explícitos quedan pendientes.
- El manifiesto registra versiones de VOC y DuckDB y, cuando hay checkout Git,
  revisión y estado de cambios sin commit. Sin Git, ambos campos de código son
  `null`; no se inventa una revisión.

## Entender la escritura

`exporter.describe_dataset()` selecciona campos publicables y calcula agregados.
`builder.build_bundle()` valida esa descripción, escribe en una carpeta temporal
hermana del destino y comprueba las filas persistidas dentro de la transacción.
Después confirma, ejecuta un checkpoint, cierra DuckDB y calcula su hash.

Solo cuando el paquete completo valida se renombra la carpeta al destino final.
Un fallo normal elimina la carpeta temporal. Una terminación abrupta del proceso
puede dejar staging oculto, pero no lo convierte en una publicación terminada.
Un bloqueo de sistema evita dos constructores simultáneos en el mismo directorio
padre en Linux/macOS. El archivo `.publication-build.lock` permanece junto a los
paquetes; no es parte de ellos ni implica que exista una operación en curso.

## Probar los límites

```sh
pytest -q tests/unit/test_publication_contracts.py tests/unit/test_publication_builder.py
```

Las pruebas usan fixtures, bases temporales y lectores controlados; no necesitan
red, credenciales ni modelos. Comprueban cantidades, versiones, privacidad,
escrituras interrumpidas, concurrencia y detección de alteraciones.

El paquete queda construido. Instalarlo en un catálogo, seleccionarlo como activo
y ofrecer consultas a la aplicación corresponde al siguiente paso.
