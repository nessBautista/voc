"""Read-only Linux resource observations for bounded corpus studies."""

import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

GIB = 1024**3
PILOT_RSS_BYTES = 3 * GIB
CONTAINER_BYTES = 8 * GIB
MARGIN_BYTES = 2 * GIB


def _number(path):
    value = path.read_text().strip()
    if not value.isdecimal():
        raise ValueError(f"Invalid resource counter: {path.name}")
    return int(value)


def _limit(path):
    return None if path.read_text().strip() == "max" else _number(path)


def _unescape(value):
    # Mountinfo uses octal escapes for spaces, tabs and backslashes.
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def _cgroup_mount(proc):
    """Find the process's unified cgroup within the visible mount namespace."""
    memberships = [
        line[3:]
        for line in (proc / "self/cgroup").read_text().splitlines()
        if line.startswith("0::")
    ]
    if len(memberships) != 1:
        raise ValueError("A unified cgroup v2 membership is required")
    member = PurePosixPath(memberships[0])
    if not member.is_absolute() or ".." in member.parts:
        raise ValueError("Cgroup membership cannot be resolved in this namespace")
    candidates = []
    for line in (proc / "self/mountinfo").read_text().splitlines():
        left, _, right = line.partition(" - ")
        if not right.startswith("cgroup2 "):
            continue
        fields = left.split()
        if len(fields) < 6:
            raise ValueError("Invalid cgroup mount record")
        root = PurePosixPath(_unescape(fields[3]))
        mount = Path(_unescape(fields[4]))
        if not root.is_absolute() or not mount.is_absolute():
            raise ValueError("Invalid cgroup mount")
        if member.is_relative_to(root):
            candidates.append((len(root.parts), mount, member.relative_to(root)))
    if not candidates:
        raise ValueError("No visible cgroup v2 mount resolves this process")
    _, mount, relative = max(candidates, key=lambda item: item[0])
    return mount, mount / str(relative)


def observe_linux(*, proc=Path("/proc"), affinity=None):
    """Read memory and CPU constraints. Unavailable observations fail closed."""
    proc = Path(proc)
    memory = {}
    for line in (proc / "meminfo").read_text().splitlines():
        name, _, value = line.partition(":")
        if name in ("MemTotal", "MemAvailable"):
            fields = value.split()
            if len(fields) != 2 or fields[1] != "kB" or not fields[0].isdecimal():
                raise ValueError("Invalid /proc memory observation")
            memory[name] = int(fields[0]) * 1024
    if set(memory) != {"MemTotal", "MemAvailable"} or not (
        0 <= memory["MemAvailable"] <= memory["MemTotal"] and memory["MemTotal"] > 0
    ):
        raise ValueError("MemTotal and MemAvailable are required")
    cpu_ids = sorted(os.sched_getaffinity(0) if affinity is None else affinity)
    if not cpu_ids or any(type(cpu) is not int or cpu < 0 for cpu in cpu_ids):
        raise ValueError("CPU affinity is unavailable")
    mount, current = _cgroup_mount(proc)
    groups = []
    while True:
        # A root hierarchy may omit controllers. The process group must expose memory.
        present = [
            (current / name).exists() for name in ("memory.current", "memory.max")
        ]
        if any(present) and not all(present):
            raise ValueError("Incomplete cgroup memory counters")
        if not groups and not all(present):
            raise ValueError("Process cgroup memory counters are required")
        entry = {"level": len(groups)}
        if all(present):
            entry.update(
                current_bytes=_number(current / "memory.current"),
                max_bytes=_limit(current / "memory.max"),
            )
        quota_file = current / "cpu.max"
        if quota_file.exists():
            quota, period = quota_file.read_text().split()
            if not period.isdecimal() or int(period) <= 0:
                raise ValueError("Invalid cgroup CPU period")
            if quota != "max" and (not quota.isdecimal() or int(quota) <= 0):
                raise ValueError("Invalid cgroup CPU quota")
            entry["cpu_quota_cores"] = (
                None if quota == "max" else int(quota) / int(period)
            )
        groups.append(entry)
        if current == mount:
            break
        current = current.parent
    # Every visible ancestor can impose a tighter bound than the leaf.
    headroom = [memory["MemAvailable"]]
    for group in groups:
        if group.get("max_bytes") is not None:
            headroom.append(max(0, group["max_bytes"] - group["current_bytes"]))
    quotas = [
        g["cpu_quota_cores"] for g in groups if g.get("cpu_quota_cores") is not None
    ]
    return {
        "host_total_bytes": memory["MemTotal"],
        "host_available_bytes": memory["MemAvailable"],
        "cgroup_current_bytes": groups[0]["current_bytes"],
        "visible_cgroups": groups,
        "available_headroom_bytes": min(headroom),
        "affinity_cpu_count": len(cpu_ids),
        "effective_cpu_capacity": min([len(cpu_ids)] + quotas),
        "visibility": "visible cgroup ancestors only; hidden ancestors may add limits",
    }


def pilot_disk_estimate(rows=5000):
    """Allow three work directories, portable releases and temporary output copies."""
    from voc.topics.files import FILE_LIMITS

    vectors = rows * 384 * 4
    selection = 256 * 1024**2
    outputs = sum(FILE_LIMITS.values())
    working = 3 * (vectors + selection + 2 * outputs)
    return {
        "policy": "three-pilot-operations-input-and-two-output-copies-v1"
        if rows == 5000
        else "three-full-operations-input-and-two-output-copies-v1",
        "planned_work_bytes": working,
        "margin_bytes": MARGIN_BYTES,
        "required_free_bytes": working + MARGIN_BYTES,
        "kind": "planning estimate, not an enforced disk quota",
    }


def resource_preflight(
    runtime_root,
    *,
    observe=None,
    disk_usage=None,
    scope="pilot",
    rows=5000,
    variant="G0",
):
    """Assess the selected budget without reserving resources or creating a run."""
    if scope not in ("pilot", "full") or variant not in ("G0", "G3", "R2"):
        raise ValueError("Invalid corpus resource scope or variant")
    if type(rows) is not int or not 20 <= rows <= 100000:
        raise ValueError("Invalid resource population size")
    if scope == "pilot" and rows != 5000:
        raise ValueError("Pilot budget requires 5000 rows")
    root = Path(runtime_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Runtime root must be an existing directory")
    observe = observe if observe is not None else observe_linux
    disk_usage = disk_usage if disk_usage is not None else shutil.disk_usage
    budget = {
        "scope": scope,
        "rows": rows,
        "max_operations": 3,
        "threads": 4,
        "worker_seconds": 600
        if scope == "pilot"
        else (1800 if variant == "R2" else 3600),
        "operation_seconds": 900
        if scope == "pilot"
        else (2400 if variant == "R2" else 4500),
        "process_tree_rss_bytes": PILOT_RSS_BYTES if scope == "pilot" else 4 * GIB,
        "container_bytes": CONTAINER_BYTES,
        "shared_margin_bytes": MARGIN_BYTES,
    }
    disk = pilot_disk_estimate(rows)
    rss_budget = budget["process_tree_rss_bytes"]
    reasons = []
    observation = None
    try:
        observation = observe()
        if observation["available_headroom_bytes"] < rss_budget + MARGIN_BYTES:
            reasons.append("insufficient_shared_memory_margin")
        if observation["cgroup_current_bytes"] + rss_budget > CONTAINER_BYTES:
            reasons.append("insufficient_container_memory_margin")
        if observation["effective_cpu_capacity"] < 1:
            reasons.append("less_than_one_cpu_available")
    except (OSError, ValueError, AttributeError) as error:
        reasons.append("resource_observation_unavailable")
        observation = {"error": str(error)}
    try:
        usage = disk_usage(root)
        disk["free_bytes"] = usage.free
        if usage.free < disk["required_free_bytes"]:
            reasons.append("insufficient_disk_margin")
    except OSError as error:
        reasons.append("disk_observation_unavailable")
        disk["error"] = str(error)
    return {
        "schema_version": "voc-topic-resource-preflight-v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "state": "resources_observed",
        "resource_budget_fits": not reasons,
        "reasons": reasons,
        "budget": budget,
        "memory_and_cpu": observation,
        "disk": disk,
        "execution_ready": False,
        "pending": [f"frozen_{scope}_protocol"],
        "limitations": [
            "Snapshot only, no resource reservation. Recheck immediately before execution.",
            "RSS supervision must cover preparation, fitting and export; sampling is not a hard limit.",
        ],
        "fits": 0,
        "model_requests": 0,
        "run_created": False,
    }
