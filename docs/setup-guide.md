# Instalación del entorno de trabajo de VOC

Esta guía te permite configurar el **workspace del Operador**, abrir Jupyter o Marimo y consultar el dataset compartido del equipo. Primero prepararás tu configuración privada; después iniciarás el contenedor y guardarás tu propio notebook.

El recorrido utiliza resultados ya publicados en S3. No necesitas recolectar reseñas, calcular embeddings ni ejecutar pipelines para completarlo. Esta es la instalación del entorno de trabajo en tu computadora; no despliega una aplicación pública en AWS.

## Antes de comenzar

Necesitas acceso al repositorio y credenciales AWS proporcionadas por el responsable del proyecto, con acceso al almacenamiento compartido y al prefijo personal que utilizarás.

Instala las herramientas de tu plataforma antes de ejecutar los scripts:

| Plataforma | Herramientas necesarias | Terminal |
| --- | --- | --- |
| macOS | Git, `just` y OrbStack o Docker Desktop con Docker CLI y Compose v2 | Terminal |
| Windows | Git for Windows con Git Bash, `just` y Docker Desktop configurado para contenedores Linux | PowerShell para la configuración inicial; Git Bash para los comandos `just` |

Inicia tu entorno Docker. No necesitas instalar Python, AWS CLI, Jupyter ni Marimo en tu computadora: se ejecutan dentro de contenedores. La modalidad de credenciales de esta guía tampoco requiere 1Password.

> **Nota:** si ya utilizas VOC, guarda tu trabajo antes de actualizarlo. Los scripts conservan la configuración existente; no migran bases de datos de versiones anteriores. Coordina con el responsable cualquier cambio de versión del entorno. El recorrido de Windows requiere su propia comprobación real; las pruebas automatizadas de los lanzadores no sustituyen esa validación.

## 1. Preparando el repositorio

Si todavía no tienes una copia, reemplaza `URL_DEL_REPOSITORIO` por la dirección que te proporcione el equipo y ejecuta:

```sh
git clone URL_DEL_REPOSITORIO voc
cd voc
```

Si ya tienes el repositorio, abre una terminal en su raíz. Utiliza la rama o etiqueta acordada con el equipo; no cambies de rama automáticamente sobre trabajo sin guardar. Antes de continuar, revisa:

```sh
git status --short
git branch --show-current
just --list
```

Esta guía corresponde a la organización que contiene [docker/operator/compose.yaml](../docker/operator/compose.yaml). Si tu copia no tiene ese archivo, verifica que utilizas la versión del proyecto que corresponde a esta guía.

La configuración del contenedor está organizada así:

```text
voc/
├── docker/
│   └── operator/
│       ├── Dockerfile
│       └── compose.yaml
├── scripts/
│   ├── setup.sh
│   ├── setup.ps1
│   ├── configure.py
│   └── start.sh
├── config/
├── notebooks/
└── Justfile
```

**Ejecuta los comandos de esta guía desde la raíz del repositorio**, donde está el `Justfile`. Los comandos `just` seleccionan el Compose del Operador y conservan la raíz como contexto de construcción y montaje. No necesitas entrar en `docker/operator/`.

**Comprobación:** `just --list` muestra `up`, `status`, `marimo` y `jupyter`.

## 2. Configurando tu acceso

En **macOS**, ejecuta:

```sh
bash scripts/setup.sh
```

En **Windows**, ejecuta desde PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Los scripts comprueban los requisitos, utilizan un contenedor temporal de Python para crear los archivos privados que falten y abren el archivo de credenciales en tu editor. No instalan herramientas en el host, cambian tu entorno Docker ni sobrescriben configuraciones existentes.

En el archivo privado `config/aws.local.env`, sustituye los ejemplos por tus credenciales:

```dotenv
AWS_ACCESS_KEY_ID='tu-access-key-id'
AWS_SECRET_ACCESS_KEY='tu-secret-access-key'
AWS_SESSION_TOKEN=''
```

Completa el token de sesión únicamente si forma parte de las credenciales que recibiste. Para leer el dataset no hace falta completar la clave opcional de OpenRouter.

Guarda el archivo y revisa estos valores en `.env`:

| Valor | Qué debes comprobar |
| --- | --- |
| `VOC_MEMBER_ID` | Te identifica dentro del proyecto; no es un usuario de AWS. |
| `VOC_AWS_AUTH_MODE` | Usa `env-file` para las credenciales literales de esta guía. |
| `VOC_AWS_ENV_FILE` | Apunta a `config/aws.local.env`. |
| `AWS_BUCKET` y `AWS_REGION` | Coinciden con el almacenamiento indicado por el equipo. |
| `VOC_DATASET_SOURCE` | Es `s3` para leer las publicaciones compartidas. |
| `VOC_ARTIFACT_DESTINATION` | Conserva `s3` si esa es la configuración de trabajo del equipo. |
| `JUPYTER_TOKEN` | Tiene el valor privado generado durante la configuración. |
| `COMPOSE_PROJECT_NAME` | Conserva el nombre de tu instalación; no lo cambies por el traslado a `docker/operator/`. |

Al crear una configuración nueva, el script toma los valores de [.env.example](../.env.example) y genera el token de notebooks. Si los archivos ya existían, debes revisar sus valores: el script los conserva.

Desde Terminal o Git Bash, comprueba que ambos archivos privados están ignorados:

```sh
git check-ignore .env config/aws.local.env
```

**Comprobación:** aparecen las dos rutas. No compartas estos archivos ni pegues sus valores en capturas o mensajes. La configuración y la alternativa con 1Password están documentadas en [config/README.md](../config/README.md).

## 3. Iniciando el workspace

En Terminal o Git Bash, ejecuta:

```sh
just up
just status
```

`just up` carga las credenciales, construye la imagen y espera a que el servicio esté listo. La primera construcción descarga dependencias de notebooks y procesamiento, por lo que puede tardar. `just status` muestra el servicio `workspace` y su estado.

**Comprobación:** el contenedor aparece como `healthy`. Esto confirma que los servicios están disponibles; la lectura de S3 se comprobará en el notebook.

## 4. Abriendo tu primer notebook

Abre el editor que quieras utilizar:

```sh
just marimo
# O bien:
just jupyter
```

Para crear un notebook personal, reemplaza `alice` por tu nombre:

```sh
just new-marimo alice/my-eda
# O bien:
just new-notebook alice/my-eda
```

El archivo queda en `notebooks/alice/`. Repetir el comando abre el mismo archivo sin reemplazar tu trabajo. Usa barras `/` en los nombres, también en Windows.

Ejecuta las celdas iniciales: en Marimo, pulsa **Load dataset**; en Jupyter, utiliza **Shift+Enter**. Puedes consultar el dataset desde una celda de Python así:

```python
from voc import collector as co

snapshot = co.get_dataset()
release_id = snapshot.info["release_id"]
print(release_id)
print(snapshot.data.shape)
snapshot.data.head()
```

VOC obtiene la publicación compartida y conserva una caché local. Debes ver un identificador de release, el tamaño del dataset y algunas filas; no esperes un número fijo de registros, porque las publicaciones pueden cambiar.

Guarda el identificador junto con tu análisis. Para repetir una lectura sobre esa misma versión, usa otra celda:

```python
pinned_snapshot = co.get_dataset(version=release_id)
print(pinned_snapshot.data.shape)
```

**Comprobación:** puedes cargar datos, identificar la versión consultada y guardar tu notebook. No has ejecutado recolección ni publicación de datasets.

## 5. Usando el entorno diariamente

La próxima vez, inicia tu entorno Docker, ejecuta `just up` y vuelve a abrir tu notebook. No necesitas repetir la configuración inicial.

| Comando | Función |
| --- | --- |
| `just up` | Construye o actualiza la imagen y levanta el workspace. |
| `just status` | Muestra el estado del contenedor. |
| `just logs` | Muestra las últimas líneas de los logs de los servicios. |
| `just shell` | Abre una shell dentro del contenedor; utiliza `exit` para salir. |
| `just marimo` / `just jupyter` | Abre el editor en el navegador. |
| `just down` | Detiene y elimina los contenedores del proyecto; conserva los archivos del repositorio montado, incluidos `notebooks/` y `data/`. |

Los puertos predeterminados son:

| Servicio | Dirección |
| --- | --- |
| Jupyter | <http://127.0.0.1:8890> |
| Marimo | <http://127.0.0.1:2721> |
| ZenML | <http://127.0.0.1:8239> |
| MLflow | <http://127.0.0.1:5002> |

Tu `.env` determina los puertos efectivos. Prefiere `just marimo` o `just jupyter` para abrir el editor con autenticación; si solicita un token, utiliza `JUPYTER_TOKEN` de tu archivo privado. ZenML y MLflow muestran tus ejecuciones y pueden estar vacíos en una instalación nueva.

Los archivos creados bajo `/workspace` dentro del contenedor corresponden a tu checkout en el host. Los archivos fuera de ese montaje pueden perderse cuando se recrea el contenedor.

### Comprobando un reinicio

Guarda el notebook, termina las operaciones que estén en ejecución y ejecuta `just down`, seguido de `just up`. Vuelve a abrir tu notebook con el mismo comando de creación. Comprueba que su contenido permanece y que puedes cargar la release guardada.

## Resolviendo problemas frecuentes

| Situación | Qué revisar |
| --- | --- |
| `just` no se encuentra | Confirma su instalación y abre una terminal nueva. En Windows, utiliza Git Bash para los comandos cotidianos. |
| Docker no responde | Inicia OrbStack o Docker Desktop y comprueba que el entorno seleccionado utiliza contenedores Linux. |
| `workspace` no está en ejecución | Ejecuta `just up` desde la raíz y revisa la salida. |
| `just status` no muestra contenedores | Consulta también los detenidos con el comando de abajo; si tampoco hay resultados, todavía no existen para ese proyecto Docker. |
| Un puerto está ocupado | Selecciona otro `VOC_*_PORT` en `.env` y vuelve a ejecutar `just up`. |
| No se abre el navegador | Usa la dirección mostrada por el lanzador y el token privado si se solicita. |
| La lectura de S3 falla | Revisa bucket, región, credenciales, token de sesión y permisos con el responsable. Después de cambiar configuración de entorno o credenciales, vuelve a ejecutar `just up`. |
| El arranque termina antes de estar listo | Revisa su salida y los logs de Docker con el comando de abajo; comparte solo fragmentos sin secretos. |

Para consultar también contenedores detenidos, desde la raíz del repositorio:

```sh
docker compose --project-directory . --env-file .env -f docker/operator/compose.yaml ps -a
```

Para ver los últimos mensajes del contenedor, incluso si el servicio ya se detuvo:

```sh
docker compose --project-directory . --env-file .env -f docker/operator/compose.yaml logs --tail 100 workspace
```

Estos comandos consultan el proyecto sin iniciarlo. Usa `just up` para el arranque habitual, porque también se encarga de cargar las credenciales.

## Puntos clave

- **Entorno:** este recorrido instala el workspace del Operador.
- **Ubicación:** la configuración Docker está en `docker/operator/`; ejecutas los comandos desde la raíz de VOC.
- **Credenciales:** `.env` y `config/aws.local.env` son privados y se conservan entre arranques.
- **Datos:** consultar una publicación compartida no requiere producir otro dataset.
- **Persistencia:** tus notebooks y datos bajo `/workspace` permanecen en el checkout del host.
- **Reproducibilidad:** guarda la release utilizada para poder volver a consultarla.

El recorrido está completo cuando puedes abrir tu notebook, leer una publicación compartida y recuperar tu trabajo después de recrear el contenedor.
