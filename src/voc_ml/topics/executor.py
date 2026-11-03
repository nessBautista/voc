"""Owned fitting subprocess with bounded runtime and explicit cancellation."""

import os
import signal
import subprocess
import sys


def _stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def bounded_worker(folder, config):
    """No shell, no retries. Timeout includes process startup and all model imports."""
    env = os.environ.copy()
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMBA_NUM_THREADS",
    ):
        env[name] = str(config.threads)
    env.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false"
    )
    with (folder / "worker.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "voc_ml.topics.worker", str(folder)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=config.timeout_seconds)
            if code != 0:
                raise RuntimeError(
                    f"Topic worker exited {code}. Inspect {folder / 'worker.log'}"
                )
        except subprocess.TimeoutExpired as error:
            _stop(process)
            raise TimeoutError(
                "Topic fit exceeded its configured wall-time limit"
            ) from error
        except BaseException:
            _stop(process)
            raise
