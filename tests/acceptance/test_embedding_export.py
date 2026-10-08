"""Opt-in export of an existing completed run; tracking reads, no S3 writes."""

import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

from voc.embeddings.validation import load_release


@pytest.mark.live
def test_completed_run_exports_a_repeatable_portable_release(tmp_path):
    run_id = os.environ.get("VOC_EMBEDDING_PRODUCER_RUN_ID")
    if not run_id:
        pytest.skip("Set VOC_EMBEDDING_PRODUCER_RUN_ID to a completed local producer")
    references = []
    for _ in range(2):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "voc_ml.cli",
                "embeddings",
                "export",
                run_id,
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        references.append(json.loads(result.stdout))
    assert references[0] == references[1]
    reference = references[0]
    detached = tmp_path / "detached-release"
    shutil.copytree(reference["directory"], detached)
    bundle = load_release(detached, manifest_sha256=reference["manifest_sha256"])
    assert bundle.info["provenance"]["producer_run_id"] == run_id
    assert bundle.info["embedding_release_id"] == reference["embedding_release_id"]
    assert len(bundle.reviews) == reference["coverage"]["selected_rows"]
    np.testing.assert_array_equal(
        bundle.vectors, np.load(reference["directory"] + "/vectors.npy")
    )
