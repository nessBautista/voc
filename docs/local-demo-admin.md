# Preparación del demo local — responsable del equipo

Este documento es para quienes preparan el acceso y los datos. Al equipo se le envía únicamente [setup-guide.md](setup-guide.md) y sus credenciales por un canal privado. Las llaves no se guardan en Git.

## Cómo funciona

El Operador obtiene y procesa el dataset. `voc publication build --source s3 --dataset-version ... --analysis ...` empaqueta los resultados ya calculados en **`manifest.json` + `publication.duckdb`**. Ese comando construye un paquete local; el upload a S3 es un paso separado. El procedimiento del piloto publica ambos archivos en:

```text
s3://voc-pilot-publications-880790861960/publications/<publication_id>/
```

El bucket de publicaciones es distinto de `voc-pilot-operator-880790861960`, donde trabaja el Operador. No hace falta entregar al equipo acceso al bucket del Operador, modelos, embeddings, backups, Secrets Manager, EC2 o Cognito.

En el despliegue existente, `docker/app/aws/voc-host install-publication` descarga ambos objetos, verifica el SHA-256 del manifiesto y llama a `voc publication activate`. La app consulta el catálogo local mediante `voc.publications`; no consulta S3 para cada petición.

El demo local reproduce esa instalación con `scripts/demo_data.py`, el mismo validador y activación atómica. El servicio efímero `prepare` recibe las credenciales S3. La app recibe solo su llave OpenRouter, sirve en loopback y usa autenticación local. Los volúmenes `voc-local-demo_catalog` y `voc-local-demo_state` conservan datos y conversaciones; no utiliza `data/voc-app`, `.env`, los scripts del Operador ni el despliegue del piloto.

`up` solo valida la copia local y arranca. `setup` construye la imagen y sincroniza; `sync` instala la versión fijada en [config/demo-publication.json](../config/demo-publication.json). Repetirlo sobre una copia válida no contacta S3. El ID y el SHA están fijados para que todos presenten el mismo análisis y una modificación inesperada en S3 no sustituya silenciosamente los datos.

## Publicación elegida y comprobación pendiente

La configuración se tomó del catálogo local y del archivo privado de despliegue de `voc-dev`:

| Dato | Valor |
| --- | --- |
| Region | `us-east-2` |
| Bucket de publicaciones | `voc-pilot-publications-880790861960` |
| Publicación | `472fd016-95f0-41c7-a9f9-209ef918062c` |
| Esquema | `voc-data-state-v2` |
| Tamaño de DuckDB | `60,829,696` bytes |
| SHA-256 del manifiesto | `46de9525b1eac23214c4a32dbe65b2eee2d125781f1bbb8653aafb03c3c3d6a5` |

La consulta al plugin AWS del 2026-11-14 falló por credenciales vencidas o inválidas. **Todavía hay que confirmar que los dos objetos de S3 coinciden con esta selección y que las llaves del equipo pueden leerlos.** No confundir la evidencia local con una comprobación del proyecto remoto.

Desde nuestro host, con AWS CLI y el perfil administrativo habitual:

```bash
aws login --profile voc-deploy --region us-east-2
aws sts get-caller-identity --profile voc-deploy --region us-east-2
aws freetier get-account-plan-state --profile voc-deploy --region us-east-2
aws s3api head-object --profile voc-deploy --region us-east-2 \
  --bucket voc-pilot-publications-880790861960 \
  --key publications/472fd016-95f0-41c7-a9f9-209ef918062c/publication.duckdb
mkdir -p data/local-demo-admin
aws s3 cp --profile voc-deploy --region us-east-2 \
  s3://voc-pilot-publications-880790861960/publications/472fd016-95f0-41c7-a9f9-209ef918062c/manifest.json \
  data/local-demo-admin/manifest.json
```

Confirma el proyecto `880790861960`, la Region, el plan y el tamaño del objeto. En AWS Settings → Billing revisa gasto y cualquier límite del plan. Comprueba el hash del manifiesto descargado (en macOS: `shasum -a 256 data/local-demo-admin/manifest.json`). Si difiere, investiga antes de modificar la configuración del repo. Revisa `ServerSideEncryption` del objeto: la plantilla existente usa SSE-S3 (`AES256`); si se cambió a KMS, hay que revisar el permiso de descifrado de esa clave concreta.

## Emitir las credenciales de lectura

El equipo no necesita una invitación de AWS Settings ni un usuario de Cognito para este demo. Sin embargo, **una access key sí debe pertenecer a una identidad técnica de IAM**. No es posible tener acceso continuo autenticado a S3 sin alguna identidad que lo autorice.

Usa una identidad técnica dedicada a esta lectura. Si ya existe, revisa todos sus permisos antes de reutilizarla: añadir una política limitada no elimina otros permisos. No distribuyas llaves de nuestro perfil de despliegue, del Operador o del agente. Una identidad por integrante permite revocar su acceso independientemente; una única identidad compartida pierde esa separación y comparte la revocación. Si la experiencia administrada del proyecto no permite esa creación, verifica la configuración en AWS Settings antes de activar capacidades avanzadas; no amplíes el proyecto solo para ejecutar esta guía.

La política preparada está en [s3-reader-policy.json](../docker/app/demo/s3-reader-policy.json). Autoriza únicamente `s3:GetObject` sobre los dos nombres de archivo bajo `publications/*/`, por HTTPS. Así permite nuevas publicaciones del demo sin dar acceso a `deployments/`, a otros buckets, a escritura ni a listados. Para limitar el acceso a una sola publicación, sustituye `*` por su UUID antes de aplicarla.

El baseline se obtuvo con IAM Policy Autopilot y se redujo al caso concreto de esta instalación:

```bash
DISABLE_IAM_POLICY_AUTOPILOT_TELEMETRY=true \
uvx iam-policy-autopilot@latest generate-policies \
  "$PWD/scripts/demo_data.py" --service-hints s3 \
  --region us-east-2 --account 880790861960 --pretty
```

La salida general incluye permisos condicionales de versiones, tags, Object Lock, Object Lambda y KMS; no se aplicó esa salida amplia. Nuestro flujo obtiene versiones actuales de dos objetos conocidos en un bucket SSE-S3. `GetObject` sin `versionId` requiere `s3:GetObject`; el `HeadObject` que utiliza la transferencia también se autoriza con ese permiso. Sin `ListBucket`, un objeto ausente puede devolver 403 en lugar de 404. [GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html), [HeadObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html).

**Las siguientes escrituras se ejecutan solo en nuestro host.** Revisa la política y el destino antes de ejecutarlas. No se ejecutaron durante la implementación. Para una identidad técnica existente y exclusiva, reemplaza `READER_NAME` por su nombre; si no existe, créala primero sin acceso a consola, según los permisos permitidos por el proyecto:

```bash
# Solo si hace falta crear la identidad técnica:
aws iam create-user --user-name READER_NAME --profile voc-deploy

aws iam put-user-policy --user-name READER_NAME \
  --policy-name VocLocalDemoRead \
  --policy-document file://docker/app/demo/s3-reader-policy.json \
  --profile voc-deploy

# No imprime el secreto en pantalla. El archivo queda fuera de Git.
# No repetir sobre una identidad ya configurada sin revisar sus llaves existentes.
(umask 077; aws iam create-access-key --user-name READER_NAME \
  --profile voc-deploy > data/local-demo-admin/READER_NAME-access-key.json)
```

Transfiere `AccessKeyId` y `SecretAccessKey` por un canal privado al destinatario. Las llaves estáticas no llevan `SessionToken`; si optamos por credenciales temporales, entrega los tres valores y su vencimiento. Revisa y rota las llaves, desactiva las que ya no se necesiten y elimina la copia de entrega cuando el destinatario la haya guardado. Nunca registres valores secretos en reportes o capturas.

Antes de entregarlas, prueba `setup` con una copia privada de esas credenciales y el archivo de configuración del demo. La prueba debe descargar realmente: una instalación que ya tiene la publicación válida reutiliza la caché y no demuestra que una nueva llave sirva. Usa otra computadora o un proyecto Docker desechable para esta comprobación, sin borrar los volúmenes de trabajo. [Gestión de access keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html).

S3 conserva su bloqueo de acceso público y cifrado; no se abre ningún servicio de AWS ni se crea un túnel. La descarga genera solicitudes y transferencia de S3; las consultas del dashboard se resuelven localmente. OpenRouter se cobra a cada llave personal. [Precios de S3](https://aws.amazon.com/s3/pricing/).

## Actualizar los datos del equipo

1. Construye y verifica una nueva publicación v2 con el flujo del Operador; no cambies bytes bajo un UUID existente.
2. Publica sus dos archivos con el procedimiento ya existente. No copies el raw store ni solo un release del dataset: el dashboard necesita el paquete con análisis.
3. Verifica la copia de S3 y actualiza `publication_id` y `manifest_sha256` en `config/demo-publication.json` dentro de `voc-dev`.
4. Entrega ese cambio de rama al equipo. Tras actualizar el repo, `sync` instala los nuevos datos. Una descarga o validación fallida deja activa la publicación anterior. `up` exige la versión acordada para no presentar inadvertidamente una anterior.
5. Abre una conversación nueva para presentar la nueva versión; las conversaciones existentes conservan sus referencias. Se retienen las publicaciones anteriores para poder consultarlas.

## Ensayo antes de distribuir

- Publicar los cambios revisados de `voc-dev`; la guía no estará disponible al hacer pull hasta ese paso.
- En Windows real, con Docker Desktop y sin Python/Node/AWS CLI/`just`: clone/pull → configure → setup → up.
- Confirmar `healthy`, dashboard, filtros y una respuesta Daily y otra de Investigación con una llave OpenRouter del integrante. La validación de inicio no hace solicitudes pagadas.
- Ejecutar stop/up y confirmar datos y conversaciones conservados.
- Probar una actualización y el rechazo de una llave inválida desde una instalación sin caché; no pegar secretos en la evidencia.
- Confirmar que `just app-up`, el Operador y el arranque AWS habitual siguen disponibles. El nuevo modo no cambia sus configuraciones.

Las pruebas automatizadas del repositorio cubren instalación y corrupción con S3 simulado, repetición sin red, persistencia de conversaciones y los lanzadores Bash/PowerShell con Docker simulado. Estas pruebas no sustituyen Docker Desktop en Windows, S3 real ni una conversación real de OpenRouter.
