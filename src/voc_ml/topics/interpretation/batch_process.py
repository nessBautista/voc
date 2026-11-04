"""Wall-time supervision of a workflow process and its transport descendants."""

import os
import signal
import subprocess
import sys


def run_process(command, *, seconds, log_path):
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            code = process.wait(timeout=seconds)
            if code:
                raise RuntimeError("Workflow failed; inspect its durable ledger")
        except BaseException:
            # This process group belongs to this invocation, never to a caller-supplied PID.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            # Kill remaining transport children even if the workflow parent has exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)
            raise


def run_workflow_process(key, *, store, seconds, log_path):
    run_process(
        [
            sys.executable,
            "-m",
            "voc_ml.topics.interpretation.batch_worker",
            key,
            str(store.root),
        ],
        seconds=seconds,
        log_path=log_path,
    )
