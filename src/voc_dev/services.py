"""Single-container local service lifecycle, also runnable without Docker."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from voc.paths import data_root
from voc.storage import load_storage
from voc_ml.tracking import server_command



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
    # Configure the stack and experiment before launching their servers.
    from .bootstrap import bootstrap

    bootstrap()
    # The repository is bind-mounted at /workspace; notebook saves reach the host.
    notebooks = Path(__file__).resolve().parents[2] / "notebooks"
    notebooks.mkdir(parents=True, exist_ok=True)
    # The versioned example is already available in notebooks/templates/.
    py = sys.executable
    commands = {
        "zenml": [
            py, "-m", "uvicorn", "zenml.zen_server.zen_server_api:app",
            "--host", "0.0.0.0", "--port", "8237",
        ],
        "mlflow": server_command(py, load_storage()),
        # Edit .py notebooks using the same installed VOC/ML environment as Jupyter.
        "marimo": [
            py,
            "-m",
            "marimo",
            "edit",
            str(notebooks),
            "--host",
            "0.0.0.0",
            "--port",
            "2718",
            "--headless",
            "--no-sandbox",
            "--skip-update-check",
        ],
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
            stdin = None
            if name == "marimo" and env.get("JUPYTER_TOKEN"):
                # Share the existing login token without putting it in CLI arguments.
                stdin = tempfile.TemporaryFile(mode="w+")  # noqa: SIM115 - closed in finally
                files.append(stdin)
                stdin.write(env["JUPYTER_TOKEN"])
                stdin.seek(0)
                command += ["--token-password-file", "-"]
            processes.append(
                (
                    name,
                    subprocess.Popen(
                        command,
                        env=env,
                        stdin=stdin,
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
