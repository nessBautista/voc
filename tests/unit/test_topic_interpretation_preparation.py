"""Interpretation inputs preserve their parent and the evidence actually sent."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner

from voc_ml.topics.interpretation.cli import interpretation
from voc_ml.topics.interpretation.preparation import (
    InputConfig,
    inspect_inputs,
    load_inputs,
    prepare_cases,
)


@pytest.fixture
def pair():
    texts = [
        "a" * 60 + "one",
        "a" * 60 + "two",
        "recovery failed",
        "recovery failed",
        "works",
    ]
    frame = pd.DataFrame(
        {"record_id": ["r1", "r2", "r3", "r4", "r5"], "model_text": texts}
    )
    topics = [
        {
            "topic_id": 0,
            "size": 5,
            "evidence_ids": ["r2", "r1"],
            "keywords": [{"term": "access", "weight": 1.0}],
        }
    ]
    info = {
        "schema_version": "voc-local-topics-v2",
        "run_id": "grouping",
        "input": {
            "selection": {"mode": "full"},
            "dataset": {"artifact_id": "dataset"},
            "embedding_release_id": "embeddings",
        },
        "configuration": {"model": {"cluster_selection_method": "leaf"}},
        "counts": {"rows": 5, "topic_count": 1},
        "files": {
            name: {"sha256": "a" * 64, "size_bytes": 1}
            for name in ("selection.parquet", "assignments.parquet", "coordinates.npy")
        },
    }
    grouping = SimpleNamespace(
        info=info,
        topics=topics,
        reviews=frame,
        assignments=pd.DataFrame({"record_id": frame.record_id, "topic_id": [0] * 5}),
    )
    refined = deepcopy(grouping)
    refined.info["schema_version"] = "voc-local-topic-representation-v2"
    refined.info["run_id"] = "refined"
    refined.topics[0]["keywords"] = [{"term": "password", "weight": 0.9}]
    refined.execution = {
        "parent_manifest_sha256": "a" * 64,
        "parent_manifest": deepcopy(info),
        "representation_config": {"method": "keybert_mmr"},
    }
    return grouping, refined


@pytest.fixture
def config():
    return InputConfig(
        Path("grouping"), "a" * 64, Path("refined"), "b" * 64, max_chars=50
    )


def reader_for(pair):
    def read(path, *, manifest_sha256):
        index = 0 if Path(path).name == "grouping" else 1
        assert manifest_sha256 == ("a" if index == 0 else "b") * 64
        return pair[index]

    return read


def test_preflight_has_no_reviews_or_execution(config, pair):
    result = inspect_inputs(config, reader=reader_for(pair))
    assert result["state"] == "inputs_verified"
    assert result["model_requests"] == 0 and result["run_created"] is False
    assert result["execution_ready"] is False
    assert "cases" not in result
    assert result["counts"] == {"rows": 5, "topic_count": 1}


def test_same_evidence_for_both_representations_and_no_mutation(config, pair):
    before = deepcopy(pair[0].topics)
    result = prepare_cases(config, [0], reader=reader_for(pair))
    case = result["cases"][0]
    # Representative order wins. Both exact duplicates and identical truncated
    # excerpts are skipped, so repeated text cannot occupy the context window.
    assert [r["record_id"] for r in case["evidence"]] == ["r2", "r3", "r5"]
    assert [r["label"] for r in case["evidence"]] == ["E1", "E2", "E3"]
    assert case["evidence"][0]["text"] == "a" * 50
    assert case["evidence"][0]["truncated"] is True
    assert case["evidence"][1]["truncated"] is False
    assert case["keywords"]["R0"][0]["term"] == "access"
    assert case["keywords"]["R2"][0]["term"] == "password"
    assert pair[0].topics == before
    assert result["partition"] == "unassigned"


def test_repeated_preparation_and_changed_evidence(config, pair):
    first = prepare_cases(config, [0], reader=reader_for(pair))
    assert prepare_cases(config, [0], reader=reader_for(pair)) == first
    pair[0].reviews.loc[2, "model_text"] = "different recovery text"
    changed = prepare_cases(config, [0], reader=reader_for(pair))
    assert changed["cases_sha256"] != first["cases_sha256"]
    assert (
        changed["cases"][0]["evidence_sha256"] != first["cases"][0]["evidence_sha256"]
    )


@pytest.mark.parametrize("ids", [[], [-1], [0, 0], [True], ["0"], list(range(13))])
def test_invalid_selection_rejected_before_reading(config, ids):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid IDs must not read files")

    with pytest.raises(ValueError):
        prepare_cases(config, ids, reader=forbidden)


def test_unknown_topic(config, pair):
    with pytest.raises(ValueError, match="Unknown topic"):
        prepare_cases(config, [99], reader=reader_for(pair))


@pytest.mark.parametrize(
    "change",
    [
        "parent_hash",
        "parent_manifest",
        "scope",
        "files",
        "evidence",
        "method",
        "schema",
    ],
)
def test_mismatched_lineage_rejected(config, pair, change):
    g, r = pair
    if change == "parent_hash":
        r.execution["parent_manifest_sha256"] = "c" * 64
    elif change == "parent_manifest":
        r.execution["parent_manifest"]["run_id"] = "other"
    elif change == "scope":
        r.info["counts"]["rows"] = 6
    elif change == "files":
        r.info["files"]["assignments.parquet"]["sha256"] = "c" * 64
    elif change == "evidence":
        r.topics[0]["evidence_ids"] = ["r5"]
    elif change == "method":
        r.execution["representation_config"]["method"] = "keybert"
    else:
        g.info["schema_version"] = "voc-local-topics-v1"
    with pytest.raises(ValueError):
        inspect_inputs(config, reader=reader_for(pair))


@pytest.mark.parametrize(
    "field,value",
    [("max_reviews", 0), ("max_chars", 49), ("max_topics", 25), ("max_reviews", True)],
)
def test_invalid_limits(field, value):
    with pytest.raises(ValueError):
        InputConfig(
            Path("grouping"), "a" * 64, Path("refined"), "b" * 64, **{field: value}
        )


def write_config(path, extra=""):
    path.write_text(
        'grouping_directory = "grouping"\n'
        'grouping_manifest_sha256 = "' + "a" * 64 + '"\n'
        'representation_directory = "refined"\n'
        'representation_manifest_sha256 = "' + "b" * 64 + '"\n' + extra
    )


def test_paths_relative_to_configuration_and_strict_fields(tmp_path):
    path = tmp_path / "inputs.toml"
    write_config(path)
    result = load_inputs(path)
    assert result.grouping_directory == tmp_path / "grouping"
    write_config(path, "live = true\n")
    with pytest.raises(ValueError, match="fields"):
        load_inputs(path)


def test_read_failure_not_hidden(config):
    def fail(*args, **kwargs):
        raise ValueError("Topic manifest checksum mismatch")

    with pytest.raises(ValueError, match="checksum"):
        inspect_inputs(config, reader=fail)


def test_actual_cli_errors_and_help_without_provider(tmp_path):
    runner = CliRunner()
    assert runner.invoke(interpretation, ["--help"]).exit_code == 0
    path = tmp_path / "inputs.toml"
    write_config(path)
    before = sorted(tmp_path.iterdir())
    result = runner.invoke(
        interpretation, ["plan", "--inputs", str(path), "--topic-id", "-1"]
    )
    assert result.exit_code != 0 and "nonnegative" in result.output
    assert sorted(tmp_path.iterdir()) == before
    result = runner.invoke(interpretation, ["plan", "--inputs", str(path), "--topic-id", "0"])
    assert result.exit_code != 0 and "Error:" in result.output
