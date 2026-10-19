"""Release integrity, portable consumption, immutable retries and export gating."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from tests.unit.test_embedding_producer import (  # noqa: F401 - shared pytest fixtures
    outputs,
    runtime,
)
from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import (
    canonical_json,
    feature_identity,
    ordered_rows_hash,
    text_hash,
)
from voc.embeddings.validation import load_release
from voc_ml.cli import main
from voc_ml.embeddings import export as exporter
from voc_ml.embeddings import producer

VERIFY_TRACKING = exporter.verify_tracking


@pytest.fixture
def completed(outputs, monkeypatch):  # noqa: F811 - pytest injects the imported fixture
    _, _, run_id = outputs
    producer.update_run(
        run_id,
        zenml_run_id=str(uuid4()),
        mlflow_run_id=uuid4().hex,
        experiment_id="test-experiment",
    )
    monkeypatch.setattr(producer, "_launch", lambda state: producer.load_run(run_id))
    assert producer.execute_run(run_id)["state"] == "completed"
    # Unit tests simulate the service boundary; live acceptance uses actual tracking.
    monkeypatch.setattr(exporter, "verify_tracking", Mock())
    return run_id


@pytest.fixture
def release(completed):
    return exporter.export_run(completed)


def manifest_of(reference):
    return json.loads((Path(reference["directory"]) / "manifest.json").read_bytes())


def rewrite_manifest(reference, manifest):
    folder = Path(reference["directory"])
    for key, name in (("vectors", "vectors.npy"), ("reviews", "reviews.parquet")):
        path = folder / name
        manifest["artifacts"][key].update(
            sha256=file_hash(path), size_bytes=path.stat().st_size
        )
    (folder / "manifest.json").write_bytes(canonical_json(manifest))


def test_release_roundtrip_and_retry_are_identical(release, completed):
    path = Path(release["directory"])
    hashes = {p.name: file_hash(p) for p in path.iterdir()}
    repeated = exporter.export_run(completed)
    assert repeated == release
    assert {p.name: file_hash(p) for p in path.iterdir()} == hashes
    bundle = load_release(path, manifest_sha256=release["manifest_sha256"])
    assert bundle.reviews.record_id.tolist() == ["a", "b", "c"]
    assert bundle.vectors.shape == (3, 384)
    assert bundle.vectors.dtype.str == "<f4"
    assert bundle.vectors.flags.c_contiguous
    assert not bundle.vectors.flags.writeable
    assert set(hashes) == {"manifest.json", "vectors.npy", "reviews.parquet"}
    assert bundle.info["provenance"]["producer_run_id"] == completed
    assert (
        str(producer.run_directory(completed))
        not in canonical_json(bundle.info).decode()
    )


def test_portable_load_without_producer_or_model_libraries(
    release, completed, tmp_path
):
    copied = tmp_path / "standalone"
    shutil.copytree(release["directory"], copied)
    producer.run_directory(completed).rename(tmp_path / "unavailable-producer")
    script = """
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'voc_ml','zenml','mlflow','torch','transformers','sentence_transformers'}:
            raise RuntimeError('Unexpected producer/model import: ' + fullname)
sys.meta_path.insert(0, Block())
from voc.embeddings.validation import load_release
bundle = load_release(sys.argv[1], manifest_sha256=sys.argv[2])
assert bundle.vectors.shape == (3, 384)
assert bundle.reviews.record_id.tolist() == ['a','b','c']
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(copied), release["manifest_sha256"]],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["vectors.npy", "reviews.parquet", "manifest.json"])
def test_truncated_artifact_rejected(release, name):
    path = Path(release["directory"]) / name
    path.write_bytes(path.read_bytes()[:10])
    with pytest.raises(ValueError):
        load_release(release["directory"], manifest_sha256=release["manifest_sha256"])


@pytest.mark.parametrize(
    "mutation", ["dtype", "dimensions", "fortran", "nonfinite", "norm", "object"]
)
def test_invalid_vectors_with_updated_checksum_rejected(release, mutation):
    manifest = manifest_of(release)
    path = Path(release["directory"]) / "vectors.npy"
    vectors = np.load(path)
    if mutation == "dtype":
        vectors = vectors.astype("float64")
    elif mutation == "dimensions":
        vectors = np.pad(vectors, ((0, 0), (0, 1)))
    elif mutation == "fortran":
        vectors = np.asfortranarray(vectors)
    elif mutation == "nonfinite":
        vectors[0, 0] = np.nan
    elif mutation == "norm":
        vectors[0, 0] = 2
    else:
        vectors = vectors.astype(object)
    np.save(path, vectors, allow_pickle=mutation == "object")
    rewrite_manifest(release, manifest)
    with pytest.raises(ValueError):
        load_release(release["directory"])


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "missing",
        "reorder",
        "vector_row",
        "source_row",
        "text",
        "hash",
        "policy",
        "extra-column",
        "blank",
    ],
)
def test_invalid_mapping_with_updated_checksum_rejected(release, mutation):
    manifest = manifest_of(release)
    path = Path(release["directory"]) / "reviews.parquet"
    frame = pd.read_parquet(path)
    if mutation == "duplicate":
        frame.loc[1, "record_id"] = "a"
    elif mutation == "missing":
        frame = frame.iloc[:-1]
    elif mutation == "reorder":
        frame = frame.iloc[::-1]
    elif mutation == "vector_row":
        frame["vector_row"] = [1, 0, 2]
    elif mutation == "source_row":
        frame["source_row"] = [0, 2, 3]
    elif mutation == "text":
        frame.loc[0, "model_text"] = "different"
    elif mutation == "hash":
        frame.loc[0, "feature_text_hash"] = "e" * 64
    elif mutation == "policy":
        frame.loc[0, "embedding_text"] = "prepared differently"
        frame.loc[0, "feature_text_hash"] = text_hash("prepared differently")
    elif mutation == "extra-column":
        frame["unexpected"] = 1
    else:
        frame.loc[0, "record_id"] = " "
    frame.to_parquet(path, index=False)
    rewrite_manifest(release, manifest)
    with pytest.raises(ValueError):
        load_release(release["directory"])


@pytest.mark.parametrize(
    "mutation", ["key", "schema", "feature", "selection", "coverage", "provenance"]
)
def test_incompatible_manifest_rejected(release, mutation):
    manifest = manifest_of(release)
    if mutation == "key":
        manifest["artifacts"]["vectors"]["key"] = "../vectors.npy"
    elif mutation == "schema":
        manifest["schema_version"] = "unknown"
    elif mutation == "feature":
        feature = manifest["feature"]
        feature["rows_sha256"] = "e" * 64
        feature["feature_id"] = feature_identity(
            manifest["selection"]["selection_id"],
            feature["policy"],
            feature["rows_sha256"],
        )
    elif mutation == "selection":
        manifest["selection"]["rows_sha256"] = "e" * 64
    elif mutation == "coverage":
        manifest["coverage"]["unique_texts"] = 3
    else:
        manifest["provenance"]["status"] = "failed"
    rewrite_manifest(release, manifest)
    with pytest.raises(ValueError):
        load_release(release["directory"])


def test_source_anchor_rejects_self_consistent_different_ids(release, completed):
    manifest = manifest_of(release)
    path = Path(release["directory"]) / "reviews.parquet"
    frame = pd.read_parquet(path)
    original = frame.copy()
    frame.loc[0, "record_id"] = "different-valid-id"
    from voc.embeddings.contracts import DatasetIdentity, InputSelection

    selection = InputSelection(
        DatasetIdentity(**manifest["dataset"]),
        len(frame),
        ordered_rows_hash(
            frame[["source_row", "record_id", "source_text_hash"]].itertuples(
                index=False, name=None
            )
        ),
    )
    manifest["selection"] = selection.as_dict()
    feature = manifest["feature"]
    feature["rows_sha256"] = ordered_rows_hash(
        frame[["record_id", "source_text_hash", "feature_text_hash"]].itertuples(
            index=False, name=None
        )
    )
    feature["feature_id"] = feature_identity(
        selection.selection_id, feature["policy"], feature["rows_sha256"]
    )
    frame.to_parquet(path, index=False)
    rewrite_manifest(release, manifest)
    with pytest.raises(ValueError, match="pinned source"):
        load_release(release["directory"], expected_reviews=original)
    with pytest.raises(ValueError, match="manifest checksum"):
        exporter.export_run(completed)


@pytest.mark.parametrize("state", ["running", "failed"])
def test_incomplete_run_cannot_allocate_release(completed, state):
    producer.update_run(completed, state=state)
    with pytest.raises(ValueError, match="Only completed"):
        exporter.export_run(completed)
    assert not (producer.run_directory(completed) / "export-intent.json").exists()


def test_tracking_failure_cannot_allocate_release(completed, monkeypatch):
    monkeypatch.setattr(
        exporter, "verify_tracking", Mock(side_effect=ValueError("tracking mismatch"))
    )
    with pytest.raises(ValueError, match="tracking mismatch"):
        exporter.export_run(completed)
    assert not (producer.run_directory(completed) / "export-intent.json").exists()


def test_interrupted_export_reuses_durable_identity(completed, monkeypatch):
    original = exporter.write_release
    monkeypatch.setattr(
        exporter, "write_release", Mock(side_effect=OSError("disk failure"))
    )
    with pytest.raises(OSError, match="disk failure"):
        exporter.export_run(completed)
    folder = producer.run_directory(completed)
    intent = json.loads((folder / "export-intent.json").read_bytes())
    assert not (folder / "export-reference.json").exists()
    monkeypatch.setattr(exporter, "write_release", original)
    assert (
        exporter.export_run(completed)["embedding_release_id"]
        == intent["embedding_release_id"]
    )


def test_lost_completion_receipt_recovers_same_release(release, completed):
    (producer.run_directory(completed) / "export-reference.json").unlink()
    assert exporter.export_run(completed) == release


def test_failed_shared_validation_leaves_no_final_directory(completed, monkeypatch):
    import voc.embeddings.export as shared

    original = shared.load_release
    monkeypatch.setattr(
        shared, "load_release", Mock(side_effect=ValueError("injected invalid output"))
    )
    with pytest.raises(ValueError, match="invalid output"):
        exporter.export_run(completed)
    folder = producer.run_directory(completed)
    assert not (folder / "release").exists() and not list(folder.glob(".release-*"))
    monkeypatch.setattr(shared, "load_release", original)
    assert exporter.export_run(completed)["state"] == "exported"


def test_changed_source_reference_never_overwrites_export(release, completed):
    state = producer.load_run(completed)
    reference = state["encoding_reference"]
    path = Path(reference["directory"]) / "encoding-report.json"
    report = json.loads(path.read_bytes())
    report["duration_seconds"] += 1
    path.write_bytes(canonical_json(report))
    reference["report_sha256"] = file_hash(path)
    producer.update_run(completed, encoding_reference=reference)
    original = file_hash(Path(release["directory"]) / "manifest.json")
    with pytest.raises(ValueError, match="refusing changed input"):
        exporter.export_run(completed)
    assert file_hash(Path(release["directory"]) / "manifest.json") == original


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_completed_export_not_silently_replaced(release, completed, damage):
    path = Path(release["directory"]) / "vectors.npy"
    if damage == "missing":
        shutil.rmtree(release["directory"])
    else:
        path.write_bytes(b"corrupt")
    with pytest.raises((OSError, ValueError)):
        exporter.export_run(completed)
    assert not path.exists() if damage == "missing" else path.read_bytes() == b"corrupt"


def test_cli_export_json_and_errors(completed, monkeypatch):
    result = CliRunner().invoke(main, ["embeddings", "export", completed, "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["state"] == "exported"
    monkeypatch.setattr(
        exporter,
        "verify_tracking",
        Mock(side_effect=ValueError("tracking unavailable")),
    )
    failed = CliRunner().invoke(main, ["embeddings", "export", completed, "--json"])
    assert failed.exit_code != 0
    assert json.loads(failed.stdout)["state"] == "error"


def test_tracking_adapter_requires_real_matching_completion(completed, monkeypatch):
    verify = VERIFY_TRACKING
    state = producer.load_run(completed)
    receipt = {
        "mlflow_run_id": state["mlflow_run_id"],
        "encoding_reference": state["encoding_reference"],
    }
    run = SimpleNamespace(
        status=SimpleNamespace(value="completed"),
        steps={
            "track_embeddings": SimpleNamespace(
                outputs={"tracking_report": [SimpleNamespace(load=lambda: receipt)]}
            )
        },
    )
    tracked = SimpleNamespace(
        info=SimpleNamespace(status="FINISHED", experiment_id=state["experiment_id"]),
        data=SimpleNamespace(
            tags={"producer_run_id": completed, "zenml_run_id": state["zenml_run_id"]}
        ),
    )
    monkeypatch.setattr(
        "zenml.client.Client", lambda: SimpleNamespace(get_pipeline_run=lambda _: run)
    )
    monkeypatch.setattr(
        "mlflow.tracking.MlflowClient",
        lambda **_: SimpleNamespace(get_run=lambda _: tracked),
    )
    monkeypatch.setattr(
        "voc.storage.load_storage", lambda: SimpleNamespace(runtime_root=Path("/tmp"))
    )
    verify(state)
    tracked.info.status = "FAILED"
    with pytest.raises(ValueError, match="MLflow"):
        verify(state)
    tracked.info.status = "FINISHED"
    tracked.data.tags["producer_run_id"] = "different"
    with pytest.raises(ValueError, match="MLflow"):
        verify(state)
    run.status.value = "failed"
    with pytest.raises(ValueError, match="ZenML"):
        verify(state)


def test_concurrent_exports_share_one_identity(completed):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def export():
        barrier.wait(timeout=10)
        return exporter.export_run(completed)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(export) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert results[0] == results[1]


def test_manifest_size_and_pinned_checksum(release):
    path = Path(release["directory"]) / "manifest.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum"):
        load_release(release["directory"], manifest_sha256=release["manifest_sha256"])
    path.write_bytes(b" " * (1024**2 + 1))
    with pytest.raises(ValueError, match="size limit"):
        load_release(release["directory"])


def test_verbatim_text_validation_accepts_equivalent_string_dtypes(release):
    manifest = manifest_of(release)
    path = Path(release["directory"]) / "reviews.parquet"
    frame = pd.read_parquet(path)
    frame["model_text"] = frame["model_text"].astype("string")
    frame.to_parquet(path, index=False)
    rewrite_manifest(release, manifest)
    assert load_release(release["directory"]).reviews.model_text.tolist() == [
        "good",
        "bad",
        "good",
    ]
