"""Single-container local service lifecycle, also runnable without Docker."""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from voc.paths import data_root


def main():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("VOC_DATA_DIR", str(root))
    os.environ.setdefault("ZENML_CONFIG_PATH", str(root / "zenml-client"))
    os.environ.setdefault(
        "ZENML_CUSTOM_SOURCE_ROOT", str(Path(__file__).resolve().parents[2])
    )
    os.environ.setdefault("ZENML_ANALYTICS_OPT_IN", "false")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    from .bootstrap import bootstrap

    bootstrap()
    notebooks = root / "notebooks"
    notebooks.mkdir(exist_ok=True)
    template = (
        Path(__file__).resolve().parents[2]
        / "notebooks/templates/workable-dataset.ipynb"
    )
    target = notebooks / template.name
    if not target.exists():
        shutil.copyfile(template, target)
    py = sys.executable
    commands = {
        "zenml": [
            py,
            "-m",
            "uvicorn",
            "zenml.zen_server.zen_server_api:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8237",
        ],
        "mlflow": [
            py,
            "-m",
            "mlflow",
            "server",
            "--backend-store-uri",
            "sqlite:///" + str(root / "mlflow/mlflow.db"),
            "--artifacts-destination",
            str(root / "mlflow/artifacts"),
            "--host",
            "0.0.0.0",
            "--port",
            "5000",
            "--workers",
            "1",
        ],
        "dashboard": [py, "-m", "voc.dashboard"],
        "jupyter": [
            py,
            "-m",
            "jupyterlab",
            "--ip=0.0.0.0",
            "--port=8888",
            "--no-browser",
            "--allow-root",
            "--ServerApp.root_dir=" + str(notebooks),
        ],
    }
    logdir = root / "logs"
    logdir.mkdir(exist_ok=True)
    processes = []
    files = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for name, command in commands.items():
            log = (logdir / (name + ".log")).open("a")
            files.append(log)
            env = os.environ.copy()
            if name == "zenml":
                env.update(
                    ZENML_SERVER_AUTH_SCHEME="NO_AUTH",
                    ZENML_SERVER_AUTO_ACTIVATE="true",
                )
            processes.append(
                (
                    name,
                    subprocess.Popen(
                        command,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    ),
                )
            )
        print("Services starting; logs: " + str(logdir), flush=True)
        while not stopping:
            for name, process in processes:
                if process.poll() is not None:
                    raise RuntimeError(
                        name + " exited; see " + str(logdir / (name + ".log"))
                    )
            time.sleep(0.5)
    finally:
        for _, process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for _, process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for f in files:
            f.close()


if __name__ == "__main__":
    main()
