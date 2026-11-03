"""Sample and cancel an owned operation group, including preparation and export."""

import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep

from .resources import CONTAINER_BYTES, PILOT_RSS_BYTES, observe_linux


def group_rss(group, *, proc=Path("/proc")):
    """Count only members of the session/group created for this operation."""
    total, members = 0, []
    page_size = os.sysconf("SC_PAGE_SIZE")
    for path in proc.iterdir():
        if not path.name.isdecimal():
            continue
        try:
            text = (path / "stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            continue
        fields = text[text.rfind(")") + 2 :].split()
        if len(fields) < 22:
            raise ValueError("Invalid process resource observation")
        if int(fields[2]) == group and int(fields[3]) == group:
            total += max(0, int(fields[21])) * page_size
            if fields[0] != "Z":
                members.append(int(path.name))
    return total, members


def stop_group(process):
    # This group is created by Popen(start_new_session=True), never chosen by the user.
    _, members = group_rss(process.pid)
    if members:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = monotonic() + 3
        while monotonic() < deadline and group_rss(process.pid)[1]:
            sleep(0.05)
        if group_rss(process.pid)[1]:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait(timeout=5)
    deadline = monotonic() + 2
    while monotonic() < deadline and group_rss(process.pid)[1]:
        sleep(0.05)
    if group_rss(process.pid)[1]:
        raise RuntimeError("Owned operation processes did not terminate")


@contextmanager
def _termination_handler():
    def interrupt(signum, frame):
        raise KeyboardInterrupt("Topic supervision received SIGTERM")

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def supervise(
    command,
    log_path,
    *,
    seconds=900,
    rss_bytes=PILOT_RSS_BYTES,
    container_bytes=CONTAINER_BYTES,
    observe=None,
    interval=0.1,
    env=None,
):
    """A sampled cancellation threshold, not an OS-enforced memory limit."""
    if not 0 < seconds <= 4500 or not 0 < interval <= 1:
        raise ValueError("Invalid supervision interval or deadline")
    if not 0 < rss_bytes <= 4 * 1024**3 or not 0 < container_bytes <= CONTAINER_BYTES:
        raise ValueError("Invalid supervision memory threshold")
    observe = observe if observe is not None else observe_linux
    started = monotonic()
    report = {
        "peak_group_rss_bytes": 0,
        "peak_cgroup_bytes": 0,
        "samples": 0,
        "sampling_seconds": interval,
        "scope": "owned operation group, summed RSS may double-count shared pages",
    }
    with _termination_handler(), Path(log_path).open("wb") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
        try:
            while True:
                rss, _ = group_rss(process.pid)
                observation = observe()
                current = observation["cgroup_current_bytes"]
                report["peak_group_rss_bytes"] = max(
                    report["peak_group_rss_bytes"], rss
                )
                report["peak_cgroup_bytes"] = max(report["peak_cgroup_bytes"], current)
                report["samples"] += 1
                if monotonic() - started > seconds:
                    raise TimeoutError("Topic operation exceeded total wall-time limit")
                if rss > rss_bytes or current > container_bytes:
                    raise MemoryError(
                        "Topic operation exceeded sampled memory threshold"
                    )
                # A visible ancestor or host may become constrained by another workload.
                if observation["available_headroom_bytes"] < 2 * 1024**3:
                    raise MemoryError("Shared memory reserve was exhausted")
                code = process.poll()
                if code is not None:
                    if code != 0:
                        raise RuntimeError(
                            f"Topic operation exited {code}. Inspect {log_path}"
                        )
                    if group_rss(process.pid)[1]:
                        raise RuntimeError(
                            "Operation exited leaving owned child processes"
                        )
                    break
                sleep(interval)
        except BaseException as error:
            report["wall_seconds"] = monotonic() - started
            error.supervision = report
            raise
        finally:
            stop_group(process)
    report["wall_seconds"] = monotonic() - started
    return report


def run_supervised(run_id, *, store):
    """Run the existing tracked workflow within one supervised session."""
    env = os.environ.copy()
    env.update(
        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false"
    )
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMBA_NUM_THREADS",
    ):
        env[name] = "4"
    folder = store.directory(run_id)
    state = store.load(run_id)
    budget = state.get("operation_budget")
    limits = (
        {}
        if budget is None
        else {
            "seconds": budget["operation_seconds"],
            "rss_bytes": budget["process_tree_rss_bytes"],
            "container_bytes": budget["container_bytes"],
        }
    )
    try:
        measurement = supervise(
            [
                sys.executable,
                "-m",
                "voc_ml.topics.corpus_worker",
                run_id,
                str(store.root),
            ],
            folder / "operation.log",
            env=env,
            **limits,
        )
        state = store.load(run_id)
        if state["state"] != "completed":
            raise ValueError("Supervised workflow did not produce a completed result")
        return store.update(run_id, supervision=measurement)
    except BaseException as error:
        # A result that raced with cancellation is preserved but not accepted as a release.
        release = folder / "release"
        if release.exists():
            release.rename(folder / "unaccepted-release")
        store.update(
            run_id,
            state="failed",
            result=None,
            error={"type": type(error).__name__, "message": str(error)[:2000]},
            supervision=getattr(error, "supervision", None),
        )
        raise
