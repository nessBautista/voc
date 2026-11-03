"""Personal lineage, local production/export and consumption without cloud access."""

import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from tests.unit.test_embedding_contracts import descriptor
from voc.datasets.shared.manifest import file_hash, frame_hash
from voc.embeddings import get_local_embeddings
from voc.embeddings.shared.contracts import (
    LOCAL_SCHEMA_VERSION,
    canonical_json,
    dataset_identity,
)
from voc.embeddings.shared.manifest import require_promotable, validate_manifest
from voc.models import Snapshot
from voc_ml.cli import main
from voc_ml.embeddings import (
    FeatureConfig,
    encode_features,
    pin_input,
    prepare_features,
    select_snapshot,
)
from voc_ml.embeddings.encoding import sentence_encoder
from voc_ml.embeddings.export import operations as exporter
from voc_ml.embeddings.production import operations as producer
from voc_ml.embeddings.shared.artifacts import read_encoding, read_input, write_input


@pytest.fixture
def personal():
    frame = pd.DataFrame(
        {
            "record_id": ["a", "b", "c"],
            "model_text": ["good", "bad", "good"],
        }
    )
    return Snapshot(
        frame,
        {
            "artifact_id": str(uuid4()),
            "run_id": str(uuid4()),
            "raw_revision_id": str(uuid4()),
            "row_count": len(frame),
            "schema_version": "workable-v1",
            "content_sha256": frame_hash(frame),
        },
    )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    return tmp_path


def test_personal_pin_resolves_once_and_checks_requested_identity(
    personal, monkeypatch
):
    reader = Mock(return_value=personal)
    monkeypatch.setattr(
        "voc_ml.embeddings.selection.operations.collector.get_dataset", reader
    )
    artifact = personal.info["artifact_id"]
    pinned = pin_input(source="personal", version=artifact)
    reader.assert_called_once_with(
        stage="prepared", version=artifact, source="personal"
    )
    assert pinned.selection.dataset.artifact_id == artifact
    assert "release_id" not in pinned.selection.dataset.as_dict()
    personal.data.loc[0, "model_text"] = "edited"
    pinned.validate()
    with pytest.raises(ValueError, match="content/row count"):
        pin_input(source="personal", version=artifact)
    personal.info["content_sha256"] = frame_hash(personal.data)
    with pytest.raises(ValueError, match="requested version"):
        pin_input(source="personal", version=str(uuid4()))


@pytest.mark.parametrize(
    "field",
    ["artifact_id", "raw_revision_id", "run_id", "content_sha256", "schema_version"],
)
def test_personal_metadata_is_required(personal, field):
    del personal.info[field]
    with pytest.raises(ValueError, match="personal metadata"):
        select_snapshot(personal, source="personal")


def test_identity_domains_cannot_be_interchanged(personal):
    with pytest.raises(ValueError, match="shared metadata"):
        select_snapshot(personal)
    personal.info["release_id"] = str(uuid4())
    with pytest.raises(ValueError, match="shared release"):
        select_snapshot(personal, source="personal")


def test_personal_lineage_changes_selection_identity(personal):
    original = select_snapshot(personal, source="personal").selection
    assert not original.promotable
    for field in ("artifact_id", "raw_revision_id", "run_id"):
        info = {**personal.info, field: str(uuid4())}
        changed = select_snapshot(
            Snapshot(personal.data, info), source="personal"
        ).selection
        assert original.selection_id != changed.selection_id
    value = original.dataset.as_dict()
    value["release_id"] = str(uuid4())
    with pytest.raises(ValueError, match="exactly"):
        dataset_identity(value)


@pytest.fixture
def build(personal, runtime, monkeypatch):
    # Different deterministic unit vectors make order mistakes observable.
    class Encoder:
        def __init__(self, profile, *args, **kwargs):
            self.resolved_revision = profile.revision

        def encode(self, texts, batch_size):
            vectors = np.zeros((len(texts), 384), dtype="<f4")
            for index, text in enumerate(texts):
                vectors[index, 0 if text == "good" else 1] = 1
            return vectors, [3] * len(texts)

    monkeypatch.setattr(sentence_encoder, "SentenceEncoder", Encoder)
    monkeypatch.setattr(sentence_encoder, "encoder_descriptor", lambda _: descriptor())
    monkeypatch.setattr(exporter, "verify_tracking", Mock())

    def make(limit=None):
        state = producer.create_run(
            source="personal",
            dataset_version=personal.info["artifact_id"],
            limit=limit,
        )
        folder = Path(state["paths"]["run"])
        pinned = select_snapshot(personal, source="personal", limit=limit)
        input_reference = write_input(pinned, folder / "input")
        assert read_input(input_reference).selection == pinned.selection
        features = prepare_features(pinned, FeatureConfig(), folder / "features")
        encoded = encode_features(
            features,
            output_dir=folder / "encoding",
            cache_path=runtime / "cache.db",
            model_dir=runtime / "models",
        )
        run_id = state["producer_run_id"]
        producer.update_run(
            run_id,
            input_reference=input_reference,
            encoding_reference=encoded,
            zenml_run_id=str(uuid4()),
            mlflow_run_id=uuid4().hex,
            experiment_id="test",
            project_identity="synthetic-test-code-identity",
        )
        monkeypatch.setattr(producer, "_launch", lambda _: producer.load_run(run_id))
        assert producer.execute_run(run_id)["state"] == "completed"
        report = read_encoding(encoded, expected_input=input_reference)
        return run_id, exporter.export_run(run_id), report

    return make


def read(personal, reference, **kwargs):
    return get_local_embeddings(
        dataset=personal,
        directory=reference["directory"],
        manifest_sha256=reference["manifest_sha256"],
        **kwargs,
    )


def test_local_production_export_retry_and_warm_cache(personal, build, monkeypatch):
    run_id, reference, first = build()
    bundle = read(personal, reference)
    assert bundle.vectors.shape == (3, 384)
    np.testing.assert_array_equal(bundle.vectors[0], bundle.vectors[2])
    assert not np.array_equal(bundle.vectors[0], bundle.vectors[1])
    assert not bundle.vectors.flags.writeable
    assert bundle.info["schema_version"] == LOCAL_SCHEMA_VERSION
    assert reference["dataset_artifact_id"] == personal.info["artifact_id"]
    assert "dataset_release_id" not in reference
    assert first["encoded_unique"] == 2 and first["reused_unique"] == 0
    assert exporter.export_run(run_id) == reference
    # Warm production must work even when loading a model is forbidden.
    monkeypatch.setattr(
        sentence_encoder,
        "SentenceEncoder",
        Mock(side_effect=AssertionError("model loaded")),
    )
    _, second_ref, second = build()
    assert second["encoded_unique"] == 0 and second["reused_unique"] == 2
    np.testing.assert_array_equal(read(personal, second_ref).vectors, bundle.vectors)


def test_local_subset_requires_explicit_opt_in(personal, build):
    _, reference, _ = build(limit=2)
    with pytest.raises(ValueError, match="allow_subset"):
        read(personal, reference)
    assert len(read(personal, reference, allow_subset=True).reviews) == 2


@pytest.mark.parametrize("change", ["artifact", "rows", "text", "outside_subset"])
def test_reader_rejects_wrong_source_even_outside_subset(personal, build, change):
    _, reference, _ = build(limit=2)
    if change == "artifact":
        personal.info["artifact_id"] = str(uuid4())
    elif change == "rows":
        personal = Snapshot(personal.data.iloc[::-1], personal.info)
    else:
        personal.data.loc[2 if change == "outside_subset" else 0, "model_text"] = (
            "changed"
        )
    with pytest.raises(ValueError):
        read(personal, reference, allow_subset=True)


@pytest.mark.parametrize(
    "filename", ["manifest.json", "reviews.parquet", "vectors.npy"]
)
def test_reader_rejects_changed_export_bytes(personal, build, filename):
    _, reference, _ = build()
    path = Path(reference["directory"]) / filename
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="checksum|size"):
        read(personal, reference)


def test_reordered_rows_rejected_even_with_new_file_hash(personal, build):
    _, reference, _ = build()
    directory = Path(reference["directory"])
    path = directory / "reviews.parquet"
    pd.read_parquet(path).iloc[::-1].to_parquet(path, index=False)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["reviews"].update(
        sha256=file_hash(path), size_bytes=path.stat().st_size
    )
    manifest_path.write_bytes(canonical_json(manifest))
    reference["manifest_sha256"] = file_hash(manifest_path)
    with pytest.raises(ValueError, match="source order"):
        read(personal, reference)


def test_local_export_cannot_promote_or_publish(build):
    from voc.embeddings.publication import publish_release

    _, reference, _ = build()
    manifest = json.loads((Path(reference["directory"]) / "manifest.json").read_text())
    with pytest.raises(ValueError, match="Personal datasets"):
        require_promotable(manifest)
    # No storage settings or client are needed to reject the incompatible origin.
    with pytest.raises(ValueError, match="Personal dataset exports"):
        publish_release(reference, None)
    wrong = deepcopy(manifest)
    wrong["schema_version"] = "voc-embeddings-v1"
    with pytest.raises(ValueError, match="schema differs"):
        validate_manifest(wrong)


def test_personal_cli_records_source_and_artifact(runtime, personal, monkeypatch):
    monkeypatch.setattr(
        producer, "_launch", Mock(side_effect=RuntimeError("no services"))
    )
    result = CliRunner().invoke(
        main,
        [
            "embeddings",
            "workflow",
            "--source",
            "personal",
            "--dataset-version",
            personal.info["artifact_id"],
            "--limit",
            "2",
            "--json",
        ],
    )
    state = json.loads(result.stdout)
    assert result.exit_code == 1 and state["state"] == "failed"
    assert state["requested"]["source"] == "personal"
    assert state["requested"]["dataset_version"] == personal.info["artifact_id"]


def test_portable_read_without_tracking_or_model_dependencies(
    personal, build, tmp_path
):
    _, reference, _ = build()
    detached = tmp_path / "detached"
    shutil.copytree(reference["directory"], detached)
    source = tmp_path / "dataset.parquet"
    personal.data.to_parquet(source, index=False)
    script = """
import importlib.abc, json, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'voc_ml','zenml','mlflow','torch','transformers','sentence_transformers','boto3'}:
            raise RuntimeError('Unexpected dependency: ' + fullname)
sys.meta_path.insert(0, Block())
import pandas as pd
from voc.models import Snapshot
from voc.embeddings import get_local_embeddings
bundle = get_local_embeddings(
    dataset=Snapshot(pd.read_parquet(sys.argv[1]), json.loads(sys.argv[2])),
    directory=sys.argv[3], manifest_sha256=sys.argv[4],
)
assert bundle.vectors.shape == (3, 384)
assert bundle.reviews.record_id.tolist() == ['a', 'b', 'c']
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(source),
            json.dumps(personal.info),
            str(detached),
            reference["manifest_sha256"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_personal_tracking_records_artifact_not_release(build, monkeypatch):
    from types import SimpleNamespace

    import mlflow

    from voc_ml.steps.embeddings import track_embeddings

    run_id, _, _ = build()
    state = producer.load_run(run_id)
    monkeypatch.setattr(
        mlflow,
        "active_run",
        lambda: SimpleNamespace(info=SimpleNamespace(run_id=state["mlflow_run_id"])),
    )
    params = Mock()
    monkeypatch.setattr(mlflow, "log_params", params)
    monkeypatch.setattr(mlflow, "set_tags", Mock())
    monkeypatch.setattr(mlflow, "log_metrics", Mock())
    monkeypatch.setattr(mlflow, "log_dict", Mock())
    receipt = track_embeddings.entrypoint(state["encoding_reference"], run_id)
    recorded = params.call_args.args[0]
    assert recorded["dataset_source"] == "personal"
    assert recorded["dataset_artifact_id"] == state["dataset"]["artifact_id"]
    assert recorded["dataset_raw_revision_id"] == state["dataset"]["raw_revision_id"]
    assert "dataset_release_id" not in recorded
    assert receipt["encoding_reference"] == state["encoding_reference"]
