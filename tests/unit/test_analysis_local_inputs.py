"""Real portable files: no cloud access, corruption detection and workflow pinning."""

import json
from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest
from click.testing import CliRunner

from tests.unit.test_analysis_inputs import fixture_inputs
from voc.analysis.local_inputs import load_local_config, read_local_inputs
from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import DatasetIdentity, InputSelection, feature_identity
from voc_ml.analysis import SelectionConfig, pin_inputs, producer, workflow
from voc_ml.cli import main


@pytest.fixture
def local_files(tmp_path, monkeypatch):
    snapshot, bundle = fixture_inputs()
    snapshot.data.to_parquet(tmp_path / "dataset.parquet", index=False)
    identity = replace(
        DatasetIdentity(**snapshot.info),
        file_sha256=file_hash(tmp_path / "dataset.parquet"),
    )
    bundle.info["dataset"] = identity.as_dict()
    selection = InputSelection(
        identity, len(snapshot.data), bundle.info["selection"]["rows_sha256"]
    )
    bundle.info["selection"] = selection.as_dict()
    bundle.info["feature"]["feature_id"] = feature_identity(
        selection.selection_id, "verbatim-v1", bundle.info["feature"]["rows_sha256"]
    )
    bundle.reviews.to_parquet(tmp_path / "reviews.parquet", index=False)
    np.save(tmp_path / "vectors.npy", bundle.vectors)
    (tmp_path / "manifest.json").write_text(json.dumps(bundle.info))
    (tmp_path / "dataset.json").write_text(
        json.dumps(
            {
                "release_id": identity.release_id,
                "workable": {
                    "row_count": identity.row_count,
                    "sha256": identity.file_sha256,
                    "content_sha256": identity.content_sha256,
                },
            }
        )
    )
    config = tmp_path / "local.toml"
    config.write_text(
        'dataset = "dataset.parquet"\ndataset_manifest = "dataset.json"\nembeddings = "."\n'
        f'dataset_manifest_sha256 = "{file_hash(tmp_path / "dataset.json")}"\n'
        f'embedding_manifest_sha256 = "{file_hash(tmp_path / "manifest.json")}"\n'
    )
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path / "runtime"))
    # Any attempted cloud read fails immediately, including a hidden fallback.
    monkeypatch.setattr(
        "voc.collector.get_dataset", Mock(side_effect=AssertionError("cloud read"))
    )
    return config


def test_local_alignment_preflight_and_workflow(local_files):
    frozen = load_local_config(local_files)
    snapshot, bundle = read_local_inputs(frozen)
    assert len(snapshot.data) == 48
    inputs = pin_inputs(
        source="local",
        local_inputs=frozen,
        selection=SelectionConfig("stratified", 24, 42),
    )
    np.testing.assert_array_equal(
        inputs.vectors, bundle.vectors[inputs.info["selection"]["positions"]]
    )
    result = CliRunner().invoke(
        main, ["analysis", "preflight-local", "--local-inputs", str(local_files)]
    )
    assert result.exit_code == 0, result.output
    assert not json.loads(result.output)["run_created"]
    state = producer.create_run(source="local", local_inputs=local_files, limit=24)
    reference = workflow.pin(state["run_id"])
    assert reference
    assert producer.load_run(state["run_id"])["inputs"]["selection"]["size"] == 24


@pytest.mark.parametrize(
    "filename",
    [
        "dataset.parquet",
        "dataset.json",
        "manifest.json",
        "vectors.npy",
        "reviews.parquet",
    ],
)
def test_changed_files_fail_instead_of_falling_back(local_files, filename):
    config = load_local_config(local_files)
    path = local_files.parent / filename
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        read_local_inputs(config)


def test_conflicting_release_and_source_rejected(local_files):
    config = load_local_config(local_files)
    with pytest.raises(ValueError, match="requested version"):
        pin_inputs(
            source="local",
            local_inputs=config,
            dataset_version="00000000-0000-4000-8000-000000000000",
        )
    with pytest.raises(ValueError, match="source=local"):
        producer.create_run(source="s3", local_inputs=local_files)


def test_sentiment_can_pin_same_local_corpus(local_files):
    state = producer.create_run(
        kind="sentiment", source="local", local_inputs=local_files, limit=8
    )
    reference = workflow.pin(state["run_id"])
    reviews, identity = workflow.read_sentiment_input(reference)
    assert len(reviews) == 8
    assert identity["dataset"]["row_count"] == 48


def test_platform_selection_precedes_limit_and_cli_accepts_repeatable_option(
    local_files, monkeypatch
):
    state = producer.create_run(
        kind="sentiment",
        source="local",
        local_inputs=local_files,
        platforms=("google_play", "app_store"),
        limit=8,
    )
    reference = workflow.pin(state["run_id"])
    reviews, identity = workflow.read_sentiment_input(reference)
    assert set(reviews.platform) <= {"google_play", "app_store"}
    assert identity["selection"]["platforms"] == ["app_store", "google_play"]
    launch = Mock()
    monkeypatch.setattr("voc_ml.analysis.cli.launch", launch)
    result = CliRunner().invoke(
        main,
        [
            "sentiment",
            "workflow",
            "--platform",
            "app_store",
            "--platform",
            "google_play",
        ],
    )
    assert result.exit_code == 0, result.output
    assert launch.call_args.kwargs["platforms"] == ("app_store", "google_play")
