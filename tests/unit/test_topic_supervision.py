"""Cancel small owned subprocesses, never provoke actual memory exhaustion."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from voc_ml.topics.supervision import group_rss, supervise


def available():
    return {"cgroup_current_bytes": 1, "available_headroom_bytes": 8 * 1024**3}


def test_short_process_succeeds_with_measurements(tmp_path):
    report = supervise(
        [sys.executable, "-c", "print('done')"],
        tmp_path / "out",
        observe=available,
        interval=0.02,
    )
    assert report["samples"] >= 1
    assert report["wall_seconds"] > 0
    assert (tmp_path / "out").read_text().strip() == "done"


@pytest.mark.parametrize("failure", ["timeout", "memory", "reserve", "observation"])
def test_failure_stops_owned_parent_and_child(tmp_path, failure):
    ids = tmp_path / "ids.json"
    command = [
        sys.executable,
        "-c",
        "import os,subprocess,sys,json,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "open(sys.argv[1],'w').write(json.dumps([os.getpid(),p.pid])); time.sleep(30)",
        str(ids),
    ]
    calls = []

    def observe():
        calls.append(1)
        if not ids.exists():
            return available()
        if failure == "memory":
            return available() | {"cgroup_current_bytes": 10 * 1024**3}
        if failure == "reserve":
            return available() | {"available_headroom_bytes": 0}
        if failure == "observation":
            raise ValueError("counter unreadable")
        return available()

    expected = {
        "timeout": TimeoutError,
        "memory": MemoryError,
        "reserve": MemoryError,
        "observation": ValueError,
    }[failure]
    with pytest.raises(expected):
        supervise(
            command,
            tmp_path / "out",
            seconds=0.5 if failure == "timeout" else 5,
            observe=observe,
            interval=0.02,
        )
    parent, child = json.loads(ids.read_text())
    assert not group_rss(parent)[1]
    # A reparented zombie can await PID 1 but cannot execute or retain its address space.
    for pid in [parent, child]:
        stat = Path(f"/proc/{pid}/stat")
        assert not stat.exists() or stat.read_text().split(") ", 1)[1].startswith("Z ")


def test_cancellation_leaves_unrelated_session_alive(tmp_path):
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        with pytest.raises(TimeoutError):
            supervise(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                tmp_path / "out",
                seconds=0.1,
                observe=available,
                interval=0.02,
            )
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_proc_group_requires_session_and_handles_parentheses(tmp_path):
    # /proc stat fields after comm: state, ppid, pgrp, session ... rss at index 21.
    for pid, session in [(100, 100), (101, 100), (102, 999)]:
        (tmp_path / str(pid)).mkdir()
        fields = ["S", "1", "100", str(session)] + ["0"] * 17 + ["2"]
        (tmp_path / str(pid) / "stat").write_text(
            f"{pid} (name (test)) " + " ".join(fields)
        )
    rss, members = group_rss(100, proc=tmp_path)
    assert set(members) == {100, 101}
    assert rss == 4 * os.sysconf("SC_PAGE_SIZE")


def test_sampled_rss_threshold_is_enforced_without_large_allocations(tmp_path):
    with pytest.raises(MemoryError) as error:
        supervise(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            tmp_path / "out",
            rss_bytes=1,
            observe=available,
            interval=0.02,
        )
    assert error.value.supervision["peak_group_rss_bytes"] > 1


def test_sigterm_to_supervisor_cleans_up_owned_operation(tmp_path):
    import signal
    import time

    pidfile = tmp_path / "child"
    script = (
        "import sys; from voc_ml.topics.supervision import supervise; "
        "supervise([sys.executable,'-c',"
        "'import os,sys,time; open(sys.argv[1],\"w\").write(str(os.getpid())); time.sleep(30)',sys.argv[1]],"
        "sys.argv[2], observe=lambda: {'cgroup_current_bytes':1,'available_headroom_bytes':8*1024**3})"
    )
    supervisor = subprocess.Popen(
        [sys.executable, "-c", script, str(pidfile), str(tmp_path / "out")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pidfile.exists()
        child = int(pidfile.read_text())
        supervisor.send_signal(signal.SIGTERM)
        assert supervisor.wait(timeout=8) != 0
        assert not group_rss(child)[1]
    finally:
        if supervisor.poll() is None:
            supervisor.terminate()
            supervisor.wait(timeout=8)
