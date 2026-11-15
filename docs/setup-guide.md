# Demo local de VOC: dashboard y chat

Vas a abrir VOC en el navegador de tu computadora. Los datos se descargan una vez y el chat usa tu propia llave de OpenRouter. No necesitas iniciar sesión en AWS ni en VOC, abrir túneles o instalar Python, Node, AWS CLI o `just`.

Estas instrucciones usan **Windows y PowerShell**. La instalación del workspace con notebooks sigue disponible en [la guía del Operador](operator-setup-guide.md).

## Antes de la sesión

Ten preparados:

- **Git for Windows** y acceso al repositorio.
- **Docker Desktop**, instalado y abierto con contenedores Linux. Completa el reinicio o la configuración de WSL que solicite su [instalador para Windows](https://docs.docker.com/desktop/setup/install/windows-install/).
- Las **credenciales de lectura de datos** que te entregue el responsable del equipo.
- Tu **llave de OpenRouter**, creada en [API Keys](https://openrouter.ai/settings/keys), y saldo disponible para el modelo acordado. Cada consulta del chat puede consumir saldo; puedes poner un límite a tu llave.
- Conexión a Internet durante la instalación, la actualización de datos y el uso del chat.

## 1. Actualiza la rama

Abre PowerShell en la carpeta de tu repositorio `voc` y ejecuta:

```powershell
git status --short
git fetch origin
git switch voc-dev
git pull --ff-only origin voc-dev
```

Si el primer comando muestra cambios tuyos, guárdalos antes de cambiar de rama. Si Git no puede cambiarla o actualizarla, detente y consulta al equipo; no borres tus cambios.

Si aún no tienes el repositorio:

```powershell
git clone --branch voc-dev https://github.com/nessBautista/voc.git
cd voc
```

**Comprueba:** `git branch --show-current` responde `voc-dev`.

## 2. Guarda tus llaves

Desde esa misma carpeta:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 configure
```

Se abre `config/demo.local.env` en el Bloc de notas. Completa el contenido entre comillas:

```dotenv
VOC_DEMO_AWS_ACCESS_KEY_ID='llave de acceso entregada por el responsable'
VOC_DEMO_AWS_SECRET_ACCESS_KEY='llave secreta entregada por el responsable'
VOC_DEMO_AWS_SESSION_TOKEN=''
VOC_DEMO_OPENROUTER_API_KEY='tu llave personal de OpenRouter'
```

Completa el token de sesión solo si te entregaron uno. Conserva el modelo y el puerto que ya aparecen en el archivo, salvo indicación del responsable. Guarda y cierra el Bloc de notas.

Este archivo es privado y Git lo ignora. No lo envíes al repositorio ni lo incluyas en capturas. Repetir `configure` abre tu configuración existente sin reemplazarla.

## 3. Prepara el ambiente

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 setup
```

La primera vez Docker descarga dependencias, construye la aplicación y obtiene los datos aprobados. Puede tardar varios minutos. No cierres la terminal mientras trabaja.

**Comprueba:** aparecen `Publicacion lista: ...` y `Preparacion completa`. Si hay un error, resuélvelo antes de continuar.

## 4. Abre el demo

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 up
```

El script espera a que la aplicación esté lista y abre el navegador:

- **Chat:** <http://localhost:8091/>
- **Dashboard:** <http://localhost:8091/dashboard/>

No hay pantalla de login. Si cambiaste el puerto, usa las direcciones que imprime el script.

En el dashboard comprueba que aparecen los datos y prueba un filtro. En el chat empieza con:

```text
¿Cómo se sienten, en general, quienes dejaron reseñas?
```

Después pregunta:

```text
¿Cuál es la queja principal?
```

Para probar **Investigación**, abre una conversación nueva, selecciona ese modo y pregunta:

```text
Encuentra el mes con mayor proporción de comentarios negativos, compara sus temas y muestra reseñas del tema destacado.
```

**Comprueba:** puedes consultar el dashboard y recibes una respuesta con evidencia. La comprobación de arranque no consume OpenRouter ni demuestra que tu llave tenga saldo: eso se confirma con la primera respuesta del chat.

## Próximas sesiones

Inicia Docker Desktop y ejecuta `up`. No necesitas volver a descargar los datos ni repetir `setup` cada día.

| Acción | Comando en PowerShell |
| --- | --- |
| Abrir | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 up` |
| Detener | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 stop` |
| Ver estado | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 status` |
| Ver últimos mensajes | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 logs` |
| Editar llaves | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 configure` |
| Actualizar datos acordados | `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\demo.ps1 sync` |

`stop` conserva datos y conversaciones en volúmenes de Docker. No elimines los volúmenes `voc-local-demo_*` ni uses `docker volume prune` si quieres conservarlos.

Cuando el equipo anuncie cambios: guarda tu trabajo, ejecuta `stop`, actualiza `voc-dev` con `git pull --ff-only origin voc-dev`, ejecuta `setup` y luego `up`. El comando `sync` instala la publicación acordada en esa versión del repo; si ya la tienes validada, la reutiliza. No elige automáticamente cualquier dato nuevo que aparezca en el almacenamiento.

El dashboard consulta la copia descargada. El chat necesita Internet y OpenRouter; detener el servicio de AWS o vencer las credenciales de descarga no impide usar datos ya instalados.

## Si algo falla

| Mensaje o síntoma | Qué hacer |
| --- | --- |
| Docker no se encuentra o no responde | Abre Docker Desktop y espera a que esté listo. Usa contenedores Linux. |
| El script no existe | Confirma la carpeta y que actualizaste `voc-dev`. |
| Credenciales AWS inválidas, vencidas o acceso denegado | Solicita al responsable que las revise. Edita `config/demo.local.env` y repite `setup` o `sync`. |
| Falta la publicación o no coincide con la versión acordada | Ejecuta `sync`. Si vuelve a fallar, comparte el mensaje con el responsable. |
| El puerto 8091 está ocupado | Cierra el servicio que lo usa o cambia `VOC_DEMO_PORT` a `8092` en el archivo privado; repite `up`. |
| El dashboard funciona pero el chat falla | Revisa tu llave, saldo y acceso al modelo en OpenRouter. Después de editar las llaves ejecuta `up` de nuevo. |
| La aplicación no queda lista | Ejecuta `status` y `logs`. Comparte solo el error, sin llaves ni archivos privados. |

## macOS o Linux

Con Git y Docker funcionando, utiliza la misma rama y configuración privada:

```bash
bash scripts/demo.sh configure
# Edita config/demo.local.env en tu editor y guarda las llaves.
bash scripts/demo.sh setup
bash scripts/demo.sh up
```

Abre las direcciones que imprime el comando. Las acciones `stop`, `status`, `logs` y `sync` son las mismas.

## Atajos con just en macOS o Linux

Si ya tienes `just`, puedes usar estos comandos desde la raíz del repo. Cada uno ejecuta el mismo script del demo descrito arriba; la instalación de Windows sigue funcionando con PowerShell sin instalar `just`.

| Acción | Comando |
| --- | --- |
| Crear o conservar el archivo privado | `just demo-configure` |
| Construir la imagen e instalar los datos | `just demo-setup` |
| Arrancar la app | `just demo-up` |
| Detener y conservar datos y conversaciones | `just demo-down` (también `just demo-stop`) |
| Ver estado | `just demo-status` |
| Ver mensajes recientes | `just demo-logs` |
| Sincronizar la publicación acordada | `just demo-sync` |

`demo-configure` no completa ni reemplaza tus llaves. En Mac abre `config/demo.local.env` con `open -e config/demo.local.env`, agrega tu llave en `VOC_DEMO_OPENROUTER_API_KEY`, guarda y ejecuta `just demo-up`. Si los datos ya están instalados, no necesitas repetir `demo-setup` por cambiar la llave.
