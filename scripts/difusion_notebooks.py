"""Serve reports with the existing diffusion environment and authenticated editors."""
from pathlib import Path
import argparse
import os
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
PORT = 2718  # Argo publishes this port on the host loopback interface.


def command(kind: str) -> list[str]:
    reports = ROOT / "reports"
    if kind == "marimo":
        return [sys.executable, "-m", "marimo", "edit", str(reports),
                "--host", "0.0.0.0", "--port", str(PORT), "--headless",
                "--token", "--skip-update-check", "--no-sandbox"]
    return [sys.executable, "-m", "jupyterlab", "--no-browser",
            "--ServerApp.ip=0.0.0.0", f"--ServerApp.port={PORT}",
            "--ServerApp.port_retries=0", f"--ServerApp.root_dir={reports}",
            "--ServerApp.default_url=/lab/tree/difusion"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["marimo", "jupyter"])
    args = parser.parse_args()
    if not (ROOT / "reports/difusion").is_dir():
        parser.error("No existe reports/difusion en este checkout.")
    # Neither editor should silently choose an unpublished port.
    try:
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", PORT))
    except OSError:
        print(f"El puerto {PORT} está ocupado. Detén el otro servidor con Ctrl+C "
              "en su terminal y vuelve a ejecutar esta acción. No se detuvo ningún proceso.", file=sys.stderr)
        return 2
    print(f"{args.kind}: {ROOT / 'reports'}", flush=True)
    print(f"Abre http://localhost:{PORT} usando el enlace con token que imprimirá el servidor.\n"
          "Mantén esta terminal abierta; Ctrl+C detiene el servidor.\n"
          "Jupyter y marimo comparten el puerto: ejecuta uno a la vez.", flush=True)
    os.environ["MARIMO_STUDIO_EDIT_ROOT"] = "marimo"
    os.environ["JUPYTER_PREFER_ENV_PATH"] = "1"
    # Keep the default authentication; neither CLI receives a token in argv.
    args_exec = command(args.kind)
    os.execv(args_exec[0], args_exec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
