"""Small stored candidates exercise completion, provenance and consumer isolation."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd
import pytest

from tests.unit.test_analysis_contracts import results
from tests.unit.test_analysis_inputs import fixture_inputs
from voc.analysis import store
from voc.datasets.manifest import file_hash
from voc_ml.analysis import SelectionConfig, select_inputs


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    pinned = select_inputs(*fixture_inputs(4))
    ids, assignments, topics, sentiments = results()
    mapping = dict(zip(ids, pinned.reviews.record_id))
    return {
        "reviews": pinned.reviews,
        "assignments": [
            replace(a, record_id=mapping[a.record_id]) for a in assignments
        ],
        "topics": [
            replace(t, evidence_ids=tuple(mapping[i] for i in t.evidence_ids))
            for t in topics
        ],
        "sentiments": [replace(s, record_id=mapping[s.record_id]) for s in sentiments],
        "inputs": pinned.info,
        "configuration": {"topics": {"backend": "bertopic"}},
        "code_sha256": "b" * 64,
        "software": {"python": "3.12", "duckdb": duckdb.__version__},
        "provenance": {"run_id": str(uuid4())},
    }


def folder(tmp_path, manifest):
    return tmp_path / "analyses" / manifest.analysis_id


def test_roundtrip_and_read_only_queries(candidate, tmp_path):
    manifest = store.write_analysis(
        **candidate, reports={"quality.json": b'{"fixture": true}'}
    )
    saved = store.read_analysis(manifest.analysis_id)
    pd.testing.assert_frame_equal(
        saved.reviews, candidate["reviews"], check_dtype=False
    )
    assert saved.assignments == tuple(candidate["assignments"])
    assert saved.sentiments == tuple(candidate["sentiments"])
    assert saved.topics == tuple(sorted(candidate["topics"], key=lambda t: t.topic_id))
    assert saved.manifest == manifest
    assert manifest.coverage == {
        "selected_reviews": 4,
        "assigned_reviews": 3,
        "unassigned_reviews": 1,
        "topic_count": 2,
        "classified": 3,
        "excluded": 1,
        "failed": 0,
    }
    assert store.list_analyses() == [manifest]
    directory = folder(tmp_path, manifest)
    assert sorted(p.name for p in directory.iterdir()) == [
        "analysis.duckdb",
        "artifacts",
        "manifest.json",
    ]
    assert not list(directory.rglob("*.npy"))
    assert (
        manifest.inputs["embedding"]["release_id"]
        == candidate["inputs"]["embedding"]["release_id"]
    )
    with store.open_analysis(manifest.analysis_id) as (con, info):
        assert info == manifest
        assert con.execute(
            "SELECT model_label, label FROM effective_sentiment WHERE status='excluded'"
        ).fetchone() == (None, None)
        assert (
            con.execute(
                "SELECT count(*) FROM effective_assignments WHERE topic_id=model_topic_id"
            ).fetchone()[0]
            == 4
        )
        with pytest.raises(duckdb.Error):
            con.execute("DELETE FROM reviews")
        with pytest.raises(duckdb.Error):
            con.execute("SELECT * FROM read_csv('/etc/passwd')")
    assert store.read_manifest(manifest.analysis_id) == manifest


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-review",
        "missing-review",
        "empty",
        "text",
        "order",
        "assignment",
        "sentiment",
        "evidence",
        "probability",
        "columns",
    ],
)
def test_invalid_results_never_create_completed_candidate(
    candidate, tmp_path, mutation
):
    if mutation == "duplicate-review":
        candidate["reviews"].loc[1, "record_id"] = candidate["reviews"].loc[
            0, "record_id"
        ]
    elif mutation == "missing-review":
        candidate["reviews"] = candidate["reviews"].iloc[:-1]
    elif mutation == "empty":
        candidate["reviews"] = candidate["reviews"].iloc[:0]
    elif mutation == "text":
        candidate["reviews"].loc[0, "model_text"] = "changed"
    elif mutation == "order":
        candidate["reviews"] = candidate["reviews"].iloc[::-1]
    elif mutation == "assignment":
        candidate["assignments"].pop()
    elif mutation == "sentiment":
        candidate["sentiments"].pop()
    elif mutation == "evidence":
        candidate["topics"][0] = replace(
            candidate["topics"][0], evidence_ids=("review-002",)
        )
    elif mutation == "probability":
        candidate["sentiments"][0].probabilities["negative"] = 0.2
    elif mutation == "columns":
        candidate["reviews"]["RECORD_ID"] = candidate["reviews"].record_id
    with pytest.raises(ValueError):
        store.write_analysis(**candidate)
    assert store.list_analyses() == []
    assert not list(tmp_path.rglob("analysis.duckdb"))


def test_repeated_write_preserves_original(candidate, tmp_path):
    manifest = store.write_analysis(**candidate)
    directory = folder(tmp_path, manifest)
    before = {p.name: file_hash(p) for p in directory.iterdir() if p.is_file()}
    with pytest.raises(FileExistsError):
        store.write_analysis(**candidate, analysis_id=manifest.analysis_id)
    assert before == {p.name: file_hash(p) for p in directory.iterdir() if p.is_file()}
    assert store.read_manifest(manifest.analysis_id) == manifest


def test_empty_destination_and_competing_writer_are_rejected(candidate, tmp_path):
    analysis_id = str(uuid4())
    directory = tmp_path / "analyses" / analysis_id
    directory.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        store.write_analysis(**candidate, analysis_id=analysis_id)
    assert directory.is_dir() and not list(directory.iterdir())
    directory.rmdir()
    lock = directory.parent / f".{analysis_id}.lock"
    lock.write_text("another writer")
    with pytest.raises(FileExistsError):
        store.write_analysis(**candidate, analysis_id=analysis_id)
    assert lock.read_text() == "another writer"
    assert store.list_analyses() == []


def test_interrupted_final_rename_hides_candidate_and_preserves_previous(
    candidate, tmp_path, monkeypatch
):
    previous = store.write_analysis(**candidate)
    original = Path.rename

    def interrupt(path, target):
        if path.name.startswith(".staging-"):
            # Even with a complete manifest, staging must remain invisible.
            assert store.list_analyses() == [previous]
            raise RuntimeError("injected failure before completion")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", interrupt)
    with pytest.raises(RuntimeError, match="injected failure"):
        store.write_analysis(**candidate)
    assert store.list_analyses() == [previous]
    assert sorted(p.name for p in (tmp_path / "analyses").iterdir()) == [
        previous.analysis_id
    ]
    assert store.read_analysis(previous.analysis_id).manifest == previous


def test_abandoned_and_incomplete_directories_are_not_listed(candidate, tmp_path):
    manifest = store.write_analysis(**candidate)
    root = tmp_path / "analyses"
    (root / str(uuid4())).mkdir()
    stage = root / ".staging-abandoned"
    stage.mkdir()
    (stage / "manifest.json").write_text("not json")
    assert store.list_analyses() == [manifest]


@pytest.mark.parametrize(
    "artifact", ["analysis.duckdb", "artifacts/report.txt", "manifest.json"]
)
def test_corruption_rejected_by_read_and_list(candidate, tmp_path, artifact):
    manifest = store.write_analysis(**candidate, reports={"report.txt": b"original"})
    path = folder(tmp_path, manifest) / artifact
    path.write_bytes(path.read_bytes() + b"corruption")
    for action in (
        lambda: store.read_analysis(manifest.analysis_id),
        store.list_analyses,
    ):
        with pytest.raises(ValueError):
            action()


def test_missing_artifact_fails_clearly(candidate, tmp_path):
    manifest = store.write_analysis(**candidate)
    (folder(tmp_path, manifest) / "analysis.duckdb").unlink()
    with pytest.raises(ValueError, match="Missing analysis artifact"):
        store.read_manifest(manifest.analysis_id)


@pytest.mark.parametrize(
    "analysis_id", ["../escape", "/tmp/escape", "", "latest", "abc", "A" * 32]
)
def test_unsafe_ids_rejected(candidate, analysis_id):
    with pytest.raises(ValueError):
        store.write_analysis(**candidate, analysis_id=analysis_id)
    with pytest.raises(ValueError):
        store.read_manifest(analysis_id)


@pytest.mark.parametrize(
    "name", ["../escape", "/tmp/escape", "x/../../manifest.json", "x\\evil"]
)
def test_unsafe_report_paths_rejected(candidate, name):
    with pytest.raises(ValueError):
        store.write_analysis(**candidate, reports={name: b"report"})


@pytest.mark.parametrize("target", ["directory", "database", "manifest", "report"])
def test_symlinks_are_not_followed(candidate, tmp_path, target):
    manifest = store.write_analysis(**candidate, reports={"report.txt": b"report"})
    directory = folder(tmp_path, manifest)
    original = (
        directory
        if target == "directory"
        else directory
        / {
            "database": "analysis.duckdb",
            "manifest": "manifest.json",
            "report": "artifacts/report.txt",
        }[target]
    )
    outside = tmp_path / "outside"
    original.rename(outside)
    original.symlink_to(outside, target_is_directory=target == "directory")
    with pytest.raises(ValueError, match="symlink"):
        store.read_manifest(manifest.analysis_id)


def test_manifest_database_metadata_mismatch(candidate, tmp_path):
    manifest = store.write_analysis(**candidate)
    path = folder(tmp_path, manifest) / "manifest.json"
    value = json.loads(path.read_text())
    value["configuration"]["topics"]["backend"] = "different"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="metadata differs"):
        store.read_analysis(manifest.analysis_id)


def test_consumer_read_without_ml_or_producer_files(candidate, tmp_path):
    manifest = store.write_analysis(**candidate)
    code = """
import importlib.abc
import sys
class BlockML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'voc_ml', 'torch', 'transformers', 'sentence_transformers', 'bertopic', 'zenml', 'mlflow'}:
            raise AssertionError('Consumer imported ' + fullname)
sys.meta_path.insert(0, BlockML())
from voc.analysis.store import read_analysis, list_analyses
saved = read_analysis(sys.argv[1])
assert len(saved.reviews) == 4
assert saved.manifest.coverage['excluded'] == 1
assert len(list_analyses()) == 1
"""
    env = dict(
        os.environ,
        VOC_DATA_DIR=str(tmp_path),
        PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"),
    )
    subprocess.run(
        [sys.executable, "-c", code, manifest.analysis_id],
        env=env,
        check=True,
        capture_output=True,
    )


def test_stratified_rows_preserve_original_embedding_positions(candidate):
    pinned = select_inputs(
        *fixture_inputs(48), selection=SelectionConfig("stratified", 4, 42)
    )
    assert pinned.reviews.vector_row.tolist() != list(range(4))
    mapping = dict(zip(candidate["reviews"].record_id, pinned.reviews.record_id))
    candidate["reviews"], candidate["inputs"] = pinned.reviews, pinned.info
    candidate["assignments"] = [
        replace(a, record_id=mapping[a.record_id]) for a in candidate["assignments"]
    ]
    candidate["sentiments"] = [
        replace(s, record_id=mapping[s.record_id]) for s in candidate["sentiments"]
    ]
    candidate["topics"] = [
        replace(t, evidence_ids=tuple(mapping[rid] for rid in t.evidence_ids))
        for t in candidate["topics"]
    ]
    manifest = store.write_analysis(**candidate)
    saved = store.read_analysis(manifest.analysis_id)
    assert saved.reviews.vector_row.tolist() == pinned.info["selection"]["positions"]
    pd.testing.assert_frame_equal(saved.reviews, pinned.reviews, check_dtype=False)
