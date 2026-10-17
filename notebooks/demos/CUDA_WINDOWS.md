# Continuar el entrenamiento en Windows con NVIDIA

Esta demo es independiente del entorno principal de la rama. Para usar una RTX
2070 Super, la ruta siguiente ejecuta Marimo directamente en Windows con CUDA;
no usa la imagen CPU del entorno principal.

## Obtener el código y los datos

```powershell
git fetch origin
git switch WOR-200-MLOps-infra
git pull --ff-only origin WOR-200-MLOps-infra
cd notebooks/demos
```

Si es una copia nueva, primero clona el repositorio. Descomprime el paquete
`sentiment-datasets.zip` en `notebooks/demos/`: sus archivos deben quedar en
`.data/sentiment/`. Los CSV no se suben a Git. El paquete no incluye credenciales,
modelos descargados ni el estado del entrenamiento en ejecución en el Mac.

## Entorno CUDA (Python 3.12)

Con un controlador NVIDIA actualizado:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print('CUDA:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'GPU no disponible')"
```

Comprueba que aparece `CUDA: True` y el nombre de la GPU antes de entrenar.
PyTorch 2.6.0 dispone de paquetes oficiales CUDA 12.4 para Windows y Linux:
https://docs.pytorch.org/get-started/previous-versions/

```powershell
.\.venv\Scripts\marimo.exe edit workflow02_sentiment.py --port 2730
```

Abre la URL que muestre Marimo. Revisa las dos rutas y las particiones, comienza
con lote de 8 (reduce a 4 si falta memoria) y pulsa **Ejecutar los dos entrenamientos**.
El código selecciona CUDA automáticamente. Cada entrenamiento empieza desde el
modelo original; esta transferencia no reanuda el proceso que sigue en el Mac.
La primera ejecución descargará RoBERTuito; el notebook de sentimiento no necesita S3.

Los bloques 02 y 03 se conservan para etiquetado manual. Todavía no se incorporan
automáticamente al dataset social; su CSV predeterminado es `Social_reviews_to_label.csv`.

## Si prefieres Docker

El Compose del entorno principal de esta rama no configura por sí solo CUDA.
Docker Desktop en Windows requiere WSL 2, acceso a la GPU desde el contenedor y
una instalación de PyTorch con CUDA compatible. No basta con copiar la imagen CPU.
Consulta https://docs.docker.com/desktop/features/gpu/ y
https://docs.docker.com/compose/how-tos/gpu-support/ .

La disponibilidad y el rendimiento de CUDA deben verificarse en el equipo NVIDIA;
las pruebas de esta transferencia se ejecutaron en CPU.
