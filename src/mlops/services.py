import os
from pathlib import Path
import signal
import subprocess
import sys


def main():
    root = Path(os.environ.get("VOC_DATA_DIR", "/data"))
    for name in ("zenml-client", "mlflow/artifacts", "notebooks", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ZENML_CONFIG_PATH", str(root / "zenml-client"))
    from zenml.client import Client
    Client()  # Initialize the local ZenML metadata store before serving it.
    py = sys.executable
    commands = {
        "zenml": [py, "-m", "uvicorn", "zenml.zen_server.zen_server_api:app",
                  "--host", "0.0.0.0", "--port", "8237"],
        "mlflow": [py, "-m", "mlflow", "server", "--backend-store-uri",
                   "sqlite:///" + str(root / "mlflow/mlflow.db"),
                   "--artifacts-destination", str(root / "mlflow/artifacts"),
                   "--host", "0.0.0.0", "--port", "5000", "--workers", "1"],
        "jupyter": [py, "-m", "jupyterlab", "--ip=0.0.0.0", "--port=8888",
                    "--no-browser", "--allow-root",
                    "--ServerApp.root_dir=" + str(root / "notebooks")],
    }
    processes, logs = [], []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for name, command in commands.items():
            log = (root / "logs" / f"{name}.log").open("a")
            logs.append(log)
            env = os.environ.copy()
            if name == "zenml":
                env.update(ZENML_SERVER_AUTH_SCHEME="NO_AUTH",
                           ZENML_SERVER_AUTO_ACTIVATE="true")
            processes.append(subprocess.Popen(command, env=env, stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True))
        print("Services starting; see /data/logs", flush=True)
        import time
        while not stopping:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("A service exited; inspect /data/logs")
            time.sleep(0.5)
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
