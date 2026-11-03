"""Resource preflight must respect shared limits and remain read-only."""

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from voc_ml.topics.cli import topics
from voc_ml.topics.resources import GIB, observe_linux, resource_preflight


def linux_fixture(tmp_path, *, member="/voc", mount_root="/"):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    mount = tmp_path / "cgroup with space"
    mount.mkdir()
    (proc / "self/cgroup").write_text(f"0::{member}\n")
    escaped = str(mount).replace(" ", r"\040")
    (proc / "self/mountinfo").write_text(
        f"1 0 0:1 {mount_root} {escaped} rw - cgroup2 cgroup rw\n"
    )
    (proc / "meminfo").write_text("MemTotal: 12582912 kB\nMemAvailable: 8388608 kB\n")
    leaf = mount / member.removeprefix(mount_root).lstrip("/")
    leaf.mkdir(exist_ok=True, parents=True)
    for directory in {mount, leaf}:
        (directory / "memory.current").write_text(str(3 * GIB))
        (directory / "memory.max").write_text("max")
        (directory / "cpu.max").write_text("max 100000")
    return proc, mount, leaf


def test_visible_ancestor_limits_are_not_mistaken_for_leaf_limits(tmp_path):
    proc, mount, leaf = linux_fixture(tmp_path)
    (leaf / "memory.max").write_text(str(10 * GIB))
    (mount / "memory.max").write_text(str(8 * GIB))
    (mount / "memory.current").write_text(str(6 * GIB))
    (mount / "cpu.max").write_text("150000 100000")
    result = observe_linux(proc=proc, affinity=range(4))
    assert result["available_headroom_bytes"] == 2 * GIB
    assert result["cgroup_current_bytes"] == 3 * GIB
    assert result["effective_cpu_capacity"] == 1.5
    assert len(result["visible_cgroups"]) == 2


@pytest.mark.parametrize("member,root", [("/", "/"), ("/docker/voc", "/docker/voc")])
def test_namespace_root_and_subtree_mounts(tmp_path, member, root):
    proc, _, _ = linux_fixture(tmp_path, member=member, mount_root=root)
    result = observe_linux(proc=proc, affinity={0, 1})
    assert result["available_headroom_bytes"] == 8 * GIB
    assert len(result["visible_cgroups"]) == 1


@pytest.mark.parametrize("fault", ["memory", "membership", "mount", "malformed_mount", "counter", "quota"])
def test_missing_or_malformed_observation_fails_closed(tmp_path, fault):
    proc, _, leaf = linux_fixture(tmp_path)
    if fault == "memory":
        (proc / "meminfo").write_text("MemTotal: 12582912 kB\n")
    elif fault == "membership":
        (proc / "self/cgroup").write_text("0::/../outside\n")
    elif fault == "mount":
        (proc / "self/mountinfo").write_text("")
    elif fault == "malformed_mount":
        (proc / "self/mountinfo").write_text("1 - cgroup2 cgroup rw\n")
    elif fault == "counter":
        (leaf / "memory.current").write_text("-1")
    else:
        (leaf / "cpu.max").write_text("-1 100000")
    report = resource_preflight(
        tmp_path,
        observe=lambda: observe_linux(proc=proc, affinity={0}),
        disk_usage=lambda _: SimpleNamespace(free=10 * GIB),
    )
    assert not report["resource_budget_fits"]
    assert "resource_observation_unavailable" in report["reasons"]
    assert not report["execution_ready"]


def resources():
    return {
        "available_headroom_bytes": 8 * GIB,
        "cgroup_current_bytes": 3 * GIB,
        "effective_cpu_capacity": 4,
    }


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("available_headroom_bytes", 5 * GIB - 1, "insufficient_shared_memory_margin"),
        ("cgroup_current_bytes", 5 * GIB + 1, "insufficient_container_memory_margin"),
        ("effective_cpu_capacity", 0.5, "less_than_one_cpu_available"),
    ],
)
def test_budget_respects_headroom_container_and_cpu(tmp_path, field, value, reason):
    observation = resources() | {field: value}
    report = resource_preflight(
        tmp_path,
        observe=lambda: observation,
        disk_usage=lambda _: SimpleNamespace(free=10 * GIB),
    )
    assert report["reasons"] == [reason]
    assert not report["resource_budget_fits"]


def test_disk_budget_covers_three_operations_and_margin(tmp_path):
    report = resource_preflight(
        tmp_path, observe=resources, disk_usage=lambda _: SimpleNamespace(free=GIB)
    )
    disk = report["disk"]
    assert disk["required_free_bytes"] == disk["planned_work_bytes"] + 2 * GIB
    assert disk["planned_work_bytes"] > 3 * 5000 * 384 * 4
    assert report["reasons"] == ["insufficient_disk_margin"]


def test_success_is_not_execution_permission_and_does_not_create_files(tmp_path):
    before = list(tmp_path.iterdir())
    report = resource_preflight(
        tmp_path, observe=resources, disk_usage=lambda _: SimpleNamespace(free=10 * GIB)
    )
    assert report["resource_budget_fits"]
    assert not report["execution_ready"]
    assert report["fits"] == report["model_requests"] == 0
    assert not report["run_created"]
    assert list(tmp_path.iterdir()) == before


def test_command_is_a_thin_adapter(tmp_path, monkeypatch):
    import voc.paths
    import voc_ml.topics.resources as module

    calls = []
    monkeypatch.setattr(voc.paths, "data_root", lambda: tmp_path)
    monkeypatch.setattr(
        module, "resource_preflight", lambda path: calls.append(path) or {"fits": 0}
    )
    result = CliRunner().invoke(topics, ["resources"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"fits": 0}
    assert calls == [tmp_path]
