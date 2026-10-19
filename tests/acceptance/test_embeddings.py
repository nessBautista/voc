"""Opt-in real producer checks using a pinned shared dataset and installed ML stack.

VOC_EMBEDDING_DATASET_VERSION must identify an existing shared release. Run setup
first. These checks write tracking reports to the configured artifact destination;
they never publish embedding releases. Each run selects only the first 1,000 rows.
"""

import json
import os
import re
import subprocess
import sys

import numpy as np
import pytest

FAULT_RUNNER = """
import sys
fault = sys.argv.pop(1)
if fault == "validation":
    import voc_ml.embeddings.artifacts as artifacts
    def fail_validation(*args, **kwargs):
        raise ValueError("injected output validation failure")
    artifacts.read_encoding = fail_validation
elif fault == "interrupt":
    import os
    import voc_ml.embeddings.artifacts as artifacts
    def stop_process(*args, **kwargs):
        os._exit(137)
    artifacts.read_encoding = stop_process
elif fault == "tracking":
    import mlflow
    def fail_tracking(*args, **kwargs):
        raise OSError("injected tracking artifact failure")
    mlflow.log_dict = fail_tracking
from voc_ml.cli import main
main()
"""


def invoke(arguments, fault=None):
    command = [sys.executable, "-m", "voc_ml.cli"]
    if fault:
        command = [sys.executable, "-c", FAULT_RUNNER, fault]
    result = subprocess.run(
        command + arguments, capture_output=True, text=True, timeout=300, check=False
    )
    try:
        state = json.loads(result.stdout)
    except ValueError as error:
        pytest.fail(
            f"CLI stdout is not JSON: {error}\n{result.stdout}\n{result.stderr}"
        )
    return result, state


@pytest.mark.live
def test_real_embedding_workflow_reuse_and_failures(tmp_path):
    release = os.environ.get("VOC_EMBEDDING_DATASET_VERSION")
    if not release:
        pytest.skip("Set VOC_EMBEDDING_DATASET_VERSION to a pinned shared release")
    args = [
        "embeddings",
        "workflow",
        "--dataset-version",
        release,
        "--limit",
        "1000",
        "--json",
    ]
    first_result, first = invoke(args)
    assert first_result.returncode == 0, first_result.stderr
    second_result, second = invoke(args)
    assert second_result.returncode == 0, second_result.stderr
    for state in (first, second):
        assert state["state"] == "completed"
        assert state["zenml_run_id"] and state["mlflow_run_id"]
        assert state["dataset"]["release_id"] == release
        assert state["selection"]["kind"] == "first-n"
        assert state["counts"]["rows"] == 1000
        result, inspected = invoke(
            ["embeddings", "inspect", state["producer_run_id"], "--json"]
        )
        assert result.returncode == 0, result.stderr
        assert inspected == state
    assert second["counts"]["encoded_unique"] == 0
    assert second["counts"]["reused_unique"] == second["counts"]["unique_texts"]
    for key in ("producer_run_id", "zenml_run_id", "mlflow_run_id"):
        assert first[key] != second[key]
    np.testing.assert_array_equal(
        np.load(first["encoding_reference"]["directory"] + "/vectors.npy"),
        np.load(second["encoding_reference"]["directory"] + "/vectors.npy"),
    )
    failures = {}
    for fault in ("validation", "tracking"):
        result, failed = invoke(args, fault=fault)
        assert result.returncode != 0
        assert failed["state"] == "failed" and "completed_at" not in failed
        assert failed["zenml_run_id"]
        assert "injected" in json.dumps(failed["step_errors"])
        if fault == "tracking":
            assert failed["mlflow_run_id"]
        result, inspected = invoke(
            ["embeddings", "inspect", failed["producer_run_id"], "--json"]
        )
        assert result.returncode != 0 and inspected["state"] == "failed"
        failures[fault] = failed
    interrupted = subprocess.run(
        [sys.executable, "-c", FAULT_RUNNER, "interrupt", *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert interrupted.returncode == 137, interrupted.stderr
    run_id = re.search(
        r"Embedding producer: ([0-9a-f-]{36})", interrupted.stderr
    ).group(1)
    result, unfinished = invoke(["embeddings", "inspect", run_id, "--json"])
    assert result.returncode != 0 and unfinished["state"] == "running"
    assert "completed_at" not in unfinished and "encoding_reference" in unfinished
    failures["interrupted_process"] = unfinished
    (tmp_path / "embedding-acceptance.json").write_text(
        json.dumps({"first": first, "warm": second, "failures": failures}, indent=2)
        + "\n"
    )
