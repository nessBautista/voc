"""Consumer-safe contracts preserve review coverage, model evidence and failures."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from tests.unit.test_analysis_inputs import fixture_inputs
from voc.analysis import (
    AnalysisManifest,
    Keyword,
    ReviewAssignment,
    ReviewSentiment,
    TopicRecord,
    validate_input_identity,
    validate_results,
)
from voc_ml.analysis import select_inputs


def classified(record_id, label="negative"):
    return ReviewSentiment(
        record_id,
        "classified",
        label,
        {k: 0.8 if k == label else 0.1 for k in ("negative", "neutral", "positive")},
        input_sha256="a" * 64,
    )


def results():
    ids = ["a", "b", "c", "d"]
    assignments = [
        ReviewAssignment("a", 0, 0.9),
        ReviewAssignment("b", 0, 0.6),
        ReviewAssignment("c", 1, 0.8),
        ReviewAssignment("d", -1, 0),
    ]
    topics = [
        TopicRecord(0, (Keyword("acceso", 0.2),), ("a",)),
        TopicRecord(1, (), ("c",)),
        TopicRecord(-1),
    ]
    sentiments = [
        classified("a"),
        classified("b"),
        classified("c", "positive"),
        ReviewSentiment("d", "excluded", reason="empty_text"),
    ]
    return ids, assignments, topics, sentiments


def test_complete_results_keep_outlier_and_unknown_sentiment():
    assert validate_results(*results()) == {
        "selected_reviews": 4,
        "assigned_reviews": 3,
        "unassigned_reviews": 1,
        "topic_count": 2,
        "classified": 3,
        "excluded": 1,
        "failed": 0,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-assignment",
        "duplicate-assignment",
        "extra-assignment",
        "missing-sentiment",
        "duplicate-sentiment",
        "cross-evidence",
        "unknown-evidence",
        "duplicate-topic",
        "missing-topic",
        "extra-topic",
    ],
)
def test_inconsistent_results_rejected(mutation):
    ids, assignments, topics, sentiments = results()
    if mutation == "missing-assignment":
        assignments.pop()
    elif mutation == "duplicate-assignment":
        assignments[-1] = assignments[0]
    elif mutation == "extra-assignment":
        assignments.append(ReviewAssignment("extra", 0, 0.8))
    elif mutation == "missing-sentiment":
        sentiments.pop()
    elif mutation == "duplicate-sentiment":
        sentiments[-1] = sentiments[0]
    elif mutation == "cross-evidence":
        topics[0] = replace(topics[0], evidence_ids=("c",))
    elif mutation == "unknown-evidence":
        topics[0] = replace(topics[0], evidence_ids=("extra",))
    elif mutation == "duplicate-topic":
        topics.append(topics[0])
    elif mutation == "missing-topic":
        topics.pop()
    elif mutation == "extra-topic":
        topics.append(TopicRecord(5))
    with pytest.raises(ValueError):
        validate_results(ids, assignments, topics, sentiments)


@pytest.mark.parametrize(
    "strength", [True, -1, 1.01, float("nan"), float("inf"), "0.5"]
)
def test_invalid_membership_strength(strength):
    with pytest.raises(ValueError):
        ReviewAssignment("a", 0, strength)


@pytest.mark.parametrize("tid", [True, -2, 1.2, "1"])
def test_invalid_topic_id(tid):
    with pytest.raises(ValueError):
        TopicRecord(tid)


@pytest.mark.parametrize(
    "probabilities",
    [
        {"negative": 0.5, "neutral": 0.5},
        {"negative": 0.4, "neutral": 0.1, "positive": 0.1},
        {"negative": float("nan"), "neutral": 0.1, "positive": 0.1},
        {"negative": 1.2, "neutral": -0.3, "positive": 0.1},
        {"negative": True, "neutral": 0, "positive": 0},
    ],
)
def test_invalid_probabilities(probabilities):
    with pytest.raises(ValueError):
        replace(classified("a"), probabilities=probabilities)


def test_failure_is_not_neutral_and_label_matches_model_distribution():
    for status in ("failed", "excluded"):
        with pytest.raises(ValueError):
            ReviewSentiment("a", status, model_label="neutral", reason="failure")
        with pytest.raises(ValueError):
            ReviewSentiment("a", status)
        assert (
            ReviewSentiment("a", status, reason="preprocessing_error").probabilities
            is None
        )
    with pytest.raises(ValueError):
        replace(classified("a"), model_label="positive")
    with pytest.raises(ValueError):
        replace(classified("a"), input_sha256=None)
    with pytest.raises(ValueError):
        replace(classified("a"), reason="unexpected")
    with pytest.raises(ValueError):
        ReviewSentiment("a", "pending")


def test_all_outliers_and_failed_reviews_are_valid_terminal_results():
    coverage = validate_results(
        ["a"],
        [ReviewAssignment("a", -1, 0)],
        [TopicRecord(-1)],
        [ReviewSentiment("a", "failed", reason="preprocessing_error")],
    )
    assert coverage["topic_count"] == 0 and coverage["failed"] == 1


def test_mutated_nested_probabilities_revalidated():
    ids, assignments, topics, sentiments = results()
    sentiments[0].probabilities["negative"] = 0
    with pytest.raises(ValueError):
        validate_results(ids, assignments, topics, sentiments)


@pytest.fixture
def manifest():
    pinned = select_inputs(*fixture_inputs(4))
    return AnalysisManifest(
        analysis_id="40f717f0-3581-4e1a-9099-3a93367d2db4",
        inputs=pinned.info,
        configuration={"topics": {"backend": "bertopic"}},
        code_sha256="b" * 64,
        software={"python": "3.12"},
        provenance={"run_id": "adee1f51-f355-47d0-a77e-b8bce93a6011"},
        artifacts={
            "database": {
                "path": "analysis.duckdb",
                "size_bytes": 100,
                "sha256": "c" * 64,
            }
        },
        coverage=validate_results(*results()),
        created_at="2026-10-10T12:00:00+00:00",
    )


def test_manifest_portable_roundtrip(manifest):
    reloaded = AnalysisManifest.from_dict(json.loads(json.dumps(manifest.as_dict())))
    assert reloaded.as_dict() == manifest.as_dict()
    # Prototype manifests carried an empty corrections list; it is dropped on read.
    legacy = {**manifest.as_dict(), "corrections": []}
    assert AnalysisManifest.from_dict(legacy).as_dict() == manifest.as_dict()


@pytest.mark.parametrize(
    "mutation",
    [
        "id",
        "self-parent",
        "schema",
        "empty-config",
        "nan-config",
        "code-hash",
        "path",
        "artifact-hash",
        "coverage",
        "status-count",
        "timezone",
        "run-id",
        "corrections",
    ],
)
def test_manifest_rejects_invalid_identity_or_coverage(manifest, mutation):
    value = manifest.as_dict()
    if mutation == "id":
        value["analysis_id"] = "../outside"
    elif mutation == "self-parent":
        value["parent_analysis_id"] = value["analysis_id"]
    elif mutation == "schema":
        value["schema_version"] = "future"
    elif mutation == "empty-config":
        value["configuration"] = {}
    elif mutation == "nan-config":
        value["configuration"] = {"value": float("nan")}
    elif mutation == "code-hash":
        value["code_sha256"] = "bad"
    elif mutation == "path":
        value["artifacts"]["database"]["path"] = "../analysis.duckdb"
    elif mutation == "artifact-hash":
        value["artifacts"]["database"]["sha256"] = "bad"
    elif mutation == "coverage":
        value["coverage"]["selected_reviews"] = 3
    elif mutation == "status-count":
        value["coverage"]["failed"] = 1
    elif mutation == "timezone":
        value["created_at"] = "2026-10-10T12:00:00"
    elif mutation == "run-id":
        value["provenance"] = {"run_id": "latest"}
    elif mutation == "corrections":
        value["corrections"] = [{"model_label": "neutral"}]
    with pytest.raises(ValueError):
        AnalysisManifest.from_dict(value)


@pytest.mark.parametrize(
    "mutation", ["strata", "order", "hash", "rows", "dtype", "release"]
)
def test_input_descriptor_consistency(manifest, mutation):
    info = deepcopy(manifest.inputs)
    if mutation == "strata":
        info["selection"]["strata"][0]["selected"] += 1
    elif mutation == "order":
        info["selection"]["positions"] = [1, 0, 2, 3]
    elif mutation == "hash":
        info["reviews_sha256"] = "bad"
    elif mutation == "rows":
        info["vector_shape"][0] = 1
    elif mutation == "dtype":
        info["vector_dtype"] = "float64"
    elif mutation == "release":
        info["embedding"]["release_id"] = "latest"
    with pytest.raises(ValueError):
        validate_input_identity(info)


def test_core_contracts_import_without_ml_or_database_libraries():
    code = """
import importlib.abc
import sys
class BlockML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'voc_ml', 'torch', 'transformers', 'sentence_transformers', 'bertopic', 'zenml', 'mlflow', 'duckdb'}:
            raise AssertionError('Core contract imported ' + fullname)
sys.meta_path.insert(0, BlockML())
from voc.analysis import ReviewAssignment, ReviewSentiment, TopicRecord, validate_results
assert validate_results(['a'], [ReviewAssignment('a', -1, 0)], [TopicRecord(-1)], [ReviewSentiment('a', 'excluded', reason='empty_text')])['selected_reviews'] == 1
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
    subprocess.run(
        [sys.executable, "-c", code], env=env, check=True, capture_output=True
    )
