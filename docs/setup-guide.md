# Instalando el entorno de trabajo de VOC-main

Configurarás tu copia de **VOC-main**, iniciarás su workspace y abrirás un notebook. Primero separarás los ajustes de los secretos; después comprobarás el almacenamiento seleccionado y la persistencia de tu trabajo.

Este recorrido no modifica `voc`, descarga reseñas ni crea recursos AWS. Empiezas con almacenamiento local. Para consultar publicaciones S3 necesitarás un destino y credenciales aprobados para este proyecto; no copies los del prototipo.

## Antes de comenzar

Abre la raíz de tu checkout `voc-main`, donde están `Justfile` y `compose.yaml`. Instala las herramientas de tu plataforma e inicia tu entorno Docker:

| Plataforma | Requisitos | Terminal |
| --- | --- | --- |
| macOS | Git, `just`, OrbStack o Docker Desktop con Compose v2 | Terminal |
| Windows | Git for Windows con Git Bash, `just`, Docker Desktop con contenedores Linux | PowerShell para setup; Git Bash para los comandos diarios |

No necesitas Python, AWS CLI, Jupyter ni Marimo en el host. Docker ejecuta Python para preparar y leer la configuración. La modalidad del equipo no necesita 1Password; la alternativa es opcional.

> **Nota:** esta guía corresponde al Compose de la raíz de `voc-main`, no a `docker/operator/` del repositorio de referencia ni al entorno independiente `notebooks/demos/`. Windows requiere una comprobación real en esa plataforma; las pruebas de configuración no la sustituyen.

## 1. Preparando tu configuración privada

Desde la raíz de `voc-main`, ejecuta el comando de tu plataforma. En macOS:

```sh
bash scripts/setup.sh
```

En Windows, desde PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Los scripts comprueban los requisitos y utilizan un contenedor temporal de Python. No instalan herramientas en tu computadora ni cambian el contexto Docker. Crea o actualiza estos archivos; los ajustes existentes tienen prioridad sobre el nombre que setup propone:

| Archivo | Contenido |
| --- | --- |
| `config/workspace.local.toml` | Tus cambios de puertos o modalidad de credenciales |
| `config/storage.local.toml` | Tu identificador y tus cambios de almacenamiento |
| `.env` | El token privado `JUPYTER_TOKEN` para los notebooks |
| `config/aws.local.env` | Credenciales literales o referencias de 1Password; inicialmente vacías |

Los archivos `.local.toml` heredan los valores omitidos de sus correspondientes `.toml` versionados. No copies todos los valores predeterminados: agrega únicamente lo que cambias.

**Comprobación:** setup termina sin errores y muestra las rutas actualizadas o conservadas, nunca sus secretos. Desde Terminal o Git Bash, ejecuta:

```sh
git check-ignore .env config/aws.local.env config/workspace.local.toml config/storage.local.toml
```

Deben aparecer las cuatro rutas. No las incluyas en commits ni capturas.

### Si ya tenías una instalación

Ejecuta el mismo setup una vez. Traslada los ajustes reconocidos de la antigua `.env` a los TOML locales, conserva el token y el archivo de credenciales, y guarda `.env.before-toml` como recuperación privada. No cambia tus datos, notebooks ni identificadores de instalación.

Si detecta ajustes contradictorios, variables desconocidas o interpolaciones en valores antiguos, se detiene antes de escribir. Revisa las rutas o claves indicadas, resuelve la contradicción y repite setup; no borres tus credenciales para continuar. El antiguo `VOC_DASHBOARD_PORT` se retira porque no corresponde a un servicio del workspace. El proyecto Compose permanece fijado a `voc-main`.

> **Nota:** migrar la configuración conserva una selección S3 existente; no la convierte automáticamente a local ni garantiza aislamiento AWS. Revisa tu destino antes del siguiente arranque. Guarda `.env.before-toml` de forma privada: contiene la configuración anterior, incluido su token.

## 2. Eligiendo los ajustes de tu instalación

Para el trabajo local actual, revisa `config/storage.local.toml`. Mantén tu `member_id` y agrega estas opciones si necesitas sobrescribir una configuración anterior:

```toml
artifact_destination = "local"
dataset_source = "personal"
bucket = ""
region = ""
```

`personal` selecciona tus datos; `local` guarda los artefactos en este checkout. `member_id` no es una identidad de AWS. Este cambio no mueve bases de datos ni descarga datos.

Si un responsable habilita después el almacenamiento compartido **aislado de VOC-main**, coloca el nombre de bucket y la región aprobados en `bucket` y `region`, y cambia `dataset_source` a `"s3"`. Cambia `artifact_destination` a `"s3"` solo cuando también debas guardar allí tus artefactos personales. Crear infraestructura y migrar artefactos anteriores son tareas separadas.

Para cambiar un puerto, agrega por ejemplo `jupyter_port = 18991` a `config/workspace.local.toml`. Los cuatro puertos deben ser distintos. Conserva el proyecto Compose fijo; los puertos predeterminados aparecen más adelante.

**Comprobación:** ningún bucket, puerto, región o selector de credenciales permanece en `.env`. Allí solo queda `JUPYTER_TOKEN`; no lo publiques.

## 3. Configurando las credenciales

### Usando archivos `.env`, sin 1Password

Esta es la modalidad predeterminada. Si necesitas seleccionarla explícitamente, coloca en `config/workspace.local.toml`:

```toml
auth_mode = "env-file"
credential_file = "config/aws.local.env"
```

Abre el archivo indicado por `credential_file`. Cuando tengas credenciales aprobadas para VOC-main, completa sus campos con los valores que recibiste:

```dotenv
AWS_ACCESS_KEY_ID='tu-access-key-id'
AWS_SECRET_ACCESS_KEY='tu-secret-access-key'
AWS_SESSION_TOKEN=''
OPENROUTER_API_KEY=''
```

Completa el token de sesión únicamente si forma parte de tus credenciales. OpenRouter es opcional. **Para el trabajo local actual, deja todos estos campos vacíos.** Mantén los secretos en una sola línea y entre comillas simples; el lanzador no ejecuta el archivo como código.

### Usando 1Password

Instala y habilita el CLI `op` en el host. En `config/workspace.local.toml`, selecciona:

```toml
auth_mode = "1password"
credential_file = "config/aws.local.env"
```

En el archivo seleccionado, reemplaza los valores de las claves necesarias por tus referencias, por ejemplo:

```dotenv
AWS_ACCESS_KEY_ID='op://VAULT/ITEM/Access Key ID'
AWS_SECRET_ACCESS_KEY='op://VAULT/ITEM/Secret Access Key'
AWS_SESSION_TOKEN=''
OPENROUTER_API_KEY=''
```

Sustituye `VAULT`, `ITEM` y los campos por los de tu bóveda. El lanzador ejecuta `op run` al iniciar el workspace; no guarda las claves resueltas en TOML. El token de notebooks permanece en `.env`. Para una ruta de credenciales personalizada, usa un archivo dentro del checkout, fuera de `data/`, con extensión `.env`.

**Comprobación:** el modo coincide con el contenido del archivo. `env-file` rechaza referencias `op://`; `just status` y `just down` no solicitan acceso a 1Password. No necesitas configurar la alternativa que no utilizas.

## 4. Iniciando y comprobando el workspace

Desde Terminal o Git Bash, ejecuta:

```sh
just up
just status
```

`just up` lee los TOML, carga las credenciales seleccionadas, construye la imagen y espera a que los servicios estén disponibles. Los lanzadores fijan el proyecto `voc-main`, su checkout y sus archivos Compose; no heredan una selección de proyecto o credenciales de otra terminal.

**Comprobación:** aparece `voc-main-workspace-1` con estado `healthy`. Esto comprueba los servicios, no el acceso a S3.

Para consultar el almacenamiento resuelto sin escribir en AWS, ejecuta:

```sh
sh scripts/workspace.sh exec -T workspace voc storage-info
```

Para la configuración local, espera `artifact_destination: "local"`, `dataset_source: "personal"` y `runtime_root: "/workspace/data"`. No uses `voc s3-check --live` como comprobación de lectura: ese comando escribe un objeto.

> **Nota:** usa `just` o `scripts/workspace.sh` para administrar este workspace. Un `docker compose` directo omite la lectura de TOML. Después de cambiar puertos, región, almacenamiento o secretos, vuelve a ejecutar `just up` para recrear el entorno efectivo.

## 5. Abriendo un notebook y conservando tu trabajo

Para abrir Jupyter, ejecuta desde la raíz:

```sh
just jupyter
```

Se abre el editor con autenticación. `just marimo` abre el otro editor. Para crear un notebook personal de Jupyter, sustituye `alice` por tu nombre:

```sh
just new-notebook alice/my-eda
```

El archivo queda en `notebooks/alice/`. Repetir el comando abre el mismo archivo sin reemplazar tu trabajo. Usa barras `/` en los nombres, también en Windows; `just new-marimo alice/my-eda` hace lo mismo para Marimo.

**Comprobación:** puedes abrir el editor y guardar un notebook. La plantilla consulta un dataset **preparado y publicado**: no ejecutes esa celda si solo tienes la base raw importada. Instalar el workspace no produce esa publicación. Sigue la guía de importación o la tarea de preparación correspondiente; no vuelvas a inicializar tu base raw existente.

### Comprobando un reinicio

Guarda el notebook, termina las operaciones activas y ejecuta:

```sh
just down
just up
just new-notebook alice/my-eda
```

`down` retira los contenedores y la red de `voc-main`, no los archivos montados. **Comprobación:** el contenido que guardaste permanece; `data/raw/raw.db` también permanece si ya existía. No pruebes esta persistencia mientras recolectas o publicas datos.

## Usando el entorno diariamente

Desde la raíz del checkout, utiliza estos comandos:

| Comando | Resultado |
| --- | --- |
| `just up` | Construye y arranca el workspace con su configuración actual |
| `just status` | Consulta los contenedores en ejecución |
| `just shell` | Abre una shell dentro del workspace; `exit` vuelve al host |
| `just logs` | Consulta los logs de los servicios en ejecución |
| `just down` | Retira los contenedores del proyecto, conservando el checkout |
| `sh scripts/workspace.sh ps -a` | Incluye contenedores detenidos |
| `sh scripts/workspace.sh logs --tail 100 workspace` | Consulta logs de Docker aunque el servicio se haya detenido |

Con los valores predeterminados, los servicios se publican únicamente en loopback:

| Servicio | Dirección |
| --- | --- |
| Jupyter | <http://127.0.0.1:8891> |
| Marimo | <http://127.0.0.1:2722> |
| ZenML | <http://127.0.0.1:8240> |
| MLflow | <http://127.0.0.1:5003> |

Los puertos efectivos proceden de tus TOML. Prefiere los lanzadores de notebooks para abrir el navegador; si solicita un token, utiliza el de tu `.env` privada.

## Resolviendo problemas

| Síntoma | Acción |
| --- | --- |
| Docker no responde | Inicia el runtime existente con contenedores Linux; no cambies el contexto automáticamente |
| Se solicita migrar `.env` | Ejecuta setup y resuelve el conflicto indicado antes de iniciar el workspace |
| Puerto ocupado | Cambia el correspondiente `*_port` en `workspace.local.toml` y ejecuta `just up` |
| Falta `op` | Instálalo si seleccionaste 1Password; el modo `env-file` no lo necesita |
| Ruta de credenciales rechazada | Utiliza un archivo `.env` dentro del checkout, fuera de `data/`; conserva el original hasta comprobar la transición |
| Error de S3 | Revisa destino, región, credenciales y permisos con el responsable; no reutilices el bucket protegido de `voc` |
| No se abre el navegador | Abre la dirección que imprime el lanzador y utiliza tu token privado |

Comparte solo mensajes sin secretos. Para consultar la precedencia exacta y los límites de las comprobaciones, lee [la referencia de configuración](../config/README.md).

## Puntos clave

- **TOML:** conserva ajustes normales; los archivos locales sobrescriben solo tus elecciones.
- **Secretos:** permanecen en archivos `.env` privados o en 1Password, nunca en TOML.
- **Equipo:** `env-file` no requiere Python ni 1Password en el host.
- **Aislamiento:** el proyecto Compose y los datos de VOC-main son independientes del checkout de referencia; eso no sustituye los permisos AWS.
- **Persistencia:** notebooks y datos bajo `/workspace` permanecen en el host después de recrear el contenedor.

La instalación está comprobada cuando puedes iniciar el workspace, consultar su configuración, guardar un notebook y recuperarlo después de recrear el contenedor. La recolección y la futura configuración S3 continúan como tareas separadas.
