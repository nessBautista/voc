"""Opt-in S3/ZenML/MLflow acceptance on the existing full embedding release.

VOC_RUN_LIVE_TESTS=1 VOC_RUN_ANALYSIS_WORKFLOW_TEST=1 pytest -q \
    tests/acceptance/test_analysis_workflow.py
Uses the configured stack, reads S3 inputs, writes personal tracking artifacts and
local candidates. Never publishes analyses or changes shared latest pointers.
"""

import json
import os
import subprocess
import sys

import pytest

from voc.analysis.queries import query
from voc_ml.analysis import artifacts, producer


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("VOC_RUN_ANALYSIS_WORKFLOW_TEST") != "1",
    reason="Explicit opt-in required for two real discovery workflow runs",
)
def test_real_fixture_workflow_and_reuse():
    command = [
        sys.executable,
        "-m",
        "voc_ml.cli",
        "analysis",
        "run",
        "--dataset-version",
        os.environ.get(
            "VOC_TEST_DATASET_VERSION", "b7a61a8b-27d8-46a7-b2ce-67d97268b741"
        ),
        "--embedding-version",
        os.environ.get(
            "VOC_TEST_EMBEDDING_VERSION", "518898e2-99df-4eb2-bb84-37e2756ecb76"
        ),
        "--limit",
        "1000",
        "--json",
    ]
    results = []
    for _ in range(2):
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=1800, check=False
        )
        assert completed.returncode == 0, completed.stderr
        state = json.loads(completed.stdout)
        assert state["state"] == "completed" and state["tracking_verified"]
        assert state["counts"]["selected_reviews"] == 1000
        assert state["counts"]["classified"] == 1000
        assert state["zenml_run_id"] and state["mlflow_run_id"]
        assert (
            producer.inspect_run(state["run_id"])["analysis_id"] == state["analysis_id"]
        )
        assert query(state["analysis_id"])["state"] == "ok"
        results.append(state)
    assert results[0]["analysis_id"] != results[1]["analysis_id"]
    assert results[0]["inputs"] == results[1]["inputs"]
    repeated = artifacts.read_json(results[1]["evidence_reference"])
    assert repeated["topics"]["reduction_reused"]
    assert repeated["sentiment"]["counts"]["computed_unique"] == 0
    assert repeated["sentiment"]["counts"]["reused_unique"] > 0
