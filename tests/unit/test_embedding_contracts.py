"""Contract checks: no models, cloud access or orchestration runs."""

import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from voc.datasets.manifest import frame_hash
from voc.embeddings.contracts import (
    SCHEMA_VERSION,
    canonical_json,
    encoder_identity,
    feature_identity,
    fingerprint,
    ordered_rows_hash,
    text_hash,
)
from voc.embeddings.manifest import require_promotable, validate_manifest
from voc.embeddings.profiles import resolve_profile
from voc.models import Snapshot
from voc_ml.embeddings import InputValidationError, pin_input, select_snapshot


@pytest.fixture
def snapshot():
    frame = pd.DataFrame(
        {
            "record_id": ["review-a", "review-b", "review-c"],
            "model_text": ["  ¡Hola! 👋  ", "Repetida", "Repetida"],
            "platform": ["play", "play", "apple"],
        }
    )
    return Snapshot(
        frame,
        {
            "release_id": "b7a61a8b-27d8-46a7-b2ce-67d97268b741",
            "content_sha256": frame_hash(frame),
            "file_sha256": "a" * 64,
            "dataset_uri": "s3://voc-test/voc/datasets",
            "row_count": len(frame),
            "schema_version": "workable-v1",
            "source": "s3",
            "stage": "prepared",
        },
    )


def descriptor():
    return {
        "profile_id": resolve_profile().profile_id,
        "adapter": "sentence-transformers-v1",
        "software": {
            "sentence-transformers": "test-version",
            "transformers": "test-version",
            "torch": "test-version",
        },
        "device": "cpu",
        "dtype": "<f4",
    }


def release_for(pinned):
    """Metadata fixture only: these checksums do not claim real artifact existence."""
    profile = resolve_profile()
    feature_rows = ordered_rows_hash(
        (row.record_id, text_hash(row.model_text), text_hash(row.model_text))
        for row in pinned.reviews.itertuples(index=False)
    )
    release = "0be47d18-60cf-49bd-bb6d-004fb3d9a9bb"
    rows = pinned.selection.row_count
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "embedding_release_id": release,
        "dataset": pinned.selection.dataset.as_dict(),
        "selection": pinned.selection.as_dict(),
        "profile": profile.as_dict(),
        "feature": {
            "policy": profile.preparation_policy,
            "rows_sha256": feature_rows,
            "feature_id": feature_identity(
                pinned.selection.selection_id, profile.preparation_policy, feature_rows
            ),
        },
        "encoder": {
            "descriptor": descriptor(),
            "encoder_id": encoder_identity(descriptor()),
        },
        "coverage": {
            "source_rows": pinned.selection.dataset.row_count,
            "selected_rows": rows,
            "unique_texts": int(pinned.reviews.model_text.nunique()),
        },
        "artifacts": {
            "vectors": {
                "key": f"releases/{release}/vectors.npy",
                "sha256": "b" * 64,
                "size_bytes": rows * 384 * 4 + 128,
                "shape": [rows, 384],
                "dtype": "<f4",
                "order": "C",
            },
            "reviews": {
                "key": f"releases/{release}/reviews.parquet",
                "sha256": "c" * 64,
                "size_bytes": 1000,
            },
        },
        "provenance": {
            "producer_run_id": "0939b68a-f917-4301-ab81-4e486c585443",
            "zenml_run_id": "d283900e-66e9-4549-b233-3ef11b98e777",
            "mlflow_run_id": "d" * 32,
            "completed_at": "2026-10-07T12:00:00+00:00",
            "status": "completed",
        },
    }
    return manifest


def test_pin_latest_resolves_once_and_survives_changed_source(snapshot, monkeypatch):
    reader = Mock(return_value=snapshot)
    monkeypatch.setattr("voc_ml.embeddings.collector.get_dataset", reader)
    pinned = pin_input()
    expected = pinned.selection.selection_id
    reader.assert_called_once_with(stage="prepared", version="latest", source="s3")
    snapshot.info["release_id"] = "813a26db-09ba-42bc-9b38-5203d2a616c2"
    snapshot.data.loc[0, "model_text"] = "new publication"
    pinned.validate()
    assert pinned.selection.selection_id == expected
    assert pinned.reviews.model_text.iloc[0] == "  ¡Hola! 👋  "
    assert reader.call_count == 1


def test_full_and_equal_sized_explicit_subset_remain_different(snapshot):
    full = select_snapshot(snapshot)
    subset = select_snapshot(snapshot, limit=len(snapshot.data))
    assert full.selection.promotable and not subset.selection.promotable
    assert full.selection.selection_id != subset.selection.selection_id
    assert full.selection.rows_sha256 == subset.selection.rows_sha256
    assert full.reviews.record_id.tolist() == snapshot.data.record_id.tolist()
    assert full.reviews.model_text.tolist() == snapshot.data.model_text.tolist()
    require_promotable(release_for(full))
    validate_manifest(release_for(subset))
    with pytest.raises(ValueError, match="Subset/test"):
        require_promotable(release_for(subset))


def test_repeated_selection_and_identities_are_stable(snapshot):
    first = select_snapshot(snapshot, limit=2)
    second = select_snapshot(snapshot, limit=2)
    assert first.selection == second.selection
    assert first.selection.selection_id == second.selection.selection_id
    assert first.reviews.source_row.tolist() == [0, 1]
    assert "source_row" not in snapshot.data
    first.validate()
    validate_manifest(release_for(first))


def test_modified_or_reordered_snapshot_rejected(snapshot):
    changed = deepcopy(snapshot)
    changed.data.loc[0, "model_text"] = "changed"
    with pytest.raises(ValueError, match="differs"):
        select_snapshot(changed)
    changed = deepcopy(snapshot)
    changed.data = changed.data.iloc[::-1]
    with pytest.raises(ValueError, match="differs"):
        select_snapshot(changed)


def test_changed_pinned_input_rejected(snapshot):
    pinned = select_snapshot(snapshot)
    pinned.reviews.loc[0, "model_text"] = "changed"
    with pytest.raises(ValueError, match="changed or reordered"):
        pinned.validate()


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_text", None),
        ("model_text", "  "),
        ("model_text", 42),
        ("record_id", None),
        ("record_id", ""),
        ("record_id", "review-a"),
    ],
)
def test_bad_input_outside_test_limit_is_not_hidden(snapshot, field, value):
    snapshot.data.loc[2, field] = value
    snapshot.info["content_sha256"] = frame_hash(snapshot.data)
    with pytest.raises(InputValidationError) as exc:
        select_snapshot(snapshot, limit=1)
    assert exc.value.report["rows"] == 3


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5, "1", 4])
def test_bad_limits_rejected(snapshot, limit):
    with pytest.raises(ValueError, match="limit"):
        select_snapshot(snapshot, limit=limit)


def test_missing_columns_and_reserved_outputs_rejected(snapshot):
    with pytest.raises(InputValidationError, match="missing_columns"):
        select_snapshot(
            Snapshot(snapshot.data.drop(columns="model_text"), snapshot.info)
        )
    snapshot.data["embedding_text"] = snapshot.data.model_text
    snapshot.info["content_sha256"] = frame_hash(snapshot.data)
    with pytest.raises(ValueError, match="reserved"):
        select_snapshot(snapshot)


def test_source_and_metadata_required_before_selection(snapshot, monkeypatch):
    reader = Mock()
    monkeypatch.setattr("voc_ml.embeddings.collector.get_dataset", reader)
    with pytest.raises(ValueError, match="shared S3"):
        pin_input(source="personal")
    reader.assert_not_called()
    del snapshot.info["content_sha256"]
    with pytest.raises(ValueError, match="missing required shared metadata"):
        select_snapshot(snapshot)


def test_input_feature_and_encoder_changes_affect_the_right_identities(snapshot):
    original = select_snapshot(snapshot)
    snapshot.data.loc[0, "model_text"] = "Changed"
    snapshot.info["content_sha256"] = frame_hash(snapshot.data)
    changed = select_snapshot(snapshot)
    assert changed.selection.selection_id != original.selection.selection_id
    rows = ordered_rows_hash([("review-a", "a" * 64, "b" * 64)])
    base_feature = feature_identity(
        original.selection.selection_id, "verbatim-v1", rows
    )
    assert base_feature != feature_identity(
        original.selection.selection_id, "strip-v1", rows
    )
    assert base_feature != feature_identity(
        changed.selection.selection_id, "verbatim-v1", rows
    )
    assert base_feature != feature_identity(
        original.selection.selection_id, "verbatim-v1", "e" * 64
    )
    encoder = descriptor()
    original_encoder = encoder_identity(encoder)
    encoder["software"]["torch"] = "next-version"
    assert encoder_identity(encoder) != original_encoder
    assert encoder["profile_id"] == resolve_profile().profile_id


def test_baseline_is_pinned_and_semantic_config_changes_profile_id():
    profile = resolve_profile()
    assert (
        profile.model == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    assert profile.revision == "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    assert (
        profile.dimensions,
        profile.dtype,
        profile.max_length,
        profile.normalize,
        profile.prompt,
    ) == (384, "<f4", 128, True, None)
    assert replace(profile, max_length=64).profile_id != profile.profile_id
    assert replace(profile, revision="f" * 40).profile_id != profile.profile_id
    assert (
        replace(profile, preparation_policy="strip-v1").profile_id != profile.profile_id
    )
    assert replace(profile, name="same-settings-v1").profile_id == profile.profile_id
    with pytest.raises(ValueError, match="Unknown"):
        resolve_profile("unknown-v1")
    with pytest.raises(ValueError, match="pinned"):
        replace(profile, revision="main")


def test_hashes_preserve_boundaries_order_and_exact_text():
    assert fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})
    assert ordered_rows_hash([("ab", "c")]) != ordered_rows_hash([("a", "bc")])
    assert ordered_rows_hash([("a",), ("b",)]) != ordered_rows_hash([("b",), ("a",)])
    assert text_hash(" Hola ") != text_hash("Hola")
    assert text_hash("é") != text_hash("e\u0301")
    with pytest.raises(ValueError):
        canonical_json({"invalid": float("nan")})


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/vectors.npy",
        "../vectors.npy",
        "a/../vectors.npy",
        "s3://other/vectors.npy",
        "a//vectors.npy",
        "a\\vectors.npy",
        "releases/other/vectors.npy",
    ],
)
def test_artifact_keys_cannot_escape_or_reference_another_release(snapshot, path):
    manifest = release_for(select_snapshot(snapshot))
    manifest["artifacts"]["vectors"]["key"] = path
    with pytest.raises(ValueError):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), "unknown-v2"),
        (("selection", "selection_id"), "e" * 64),
        (("selection", "row_count"), 2),
        (("selection", "row_count"), True),
        (("coverage", "source_rows"), 4),
        (("coverage", "selected_rows"), 2),
        (("coverage", "unique_texts"), 4),
        (("feature", "feature_id"), "e" * 64),
        (("profile", "config", "normalize"), 1),
        (("encoder", "encoder_id"), "e" * 64),
        (("artifacts", "reviews", "sha256"), "bad-hash"),
        (("artifacts", "vectors", "dtype"), "<f8"),
        (("artifacts", "vectors", "shape"), [3, 385]),
        (("artifacts", "vectors", "size_bytes"), 100),
        (("artifacts", "reviews", "size_bytes"), 2**30 + 1),
        (("provenance", "status"), "failed"),
        (("provenance", "completed_at"), "2026-10-07T12:00:00"),
        (("provenance", "mlflow_run_id"), "invalid"),
    ],
)
def test_inconsistent_release_metadata_rejected(snapshot, path, value):
    manifest = release_for(select_snapshot(snapshot))
    target = manifest
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate_manifest(manifest)


def test_core_and_selection_import_without_ml_dependencies():
    script = """
import importlib.abc
import sys
class NoML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'torch', 'sentence_transformers', 'transformers', 'zenml', 'mlflow'}:
            raise AssertionError('Unexpected ML dependency: ' + fullname)
sys.meta_path.insert(0, NoML())
from voc.embeddings import resolve_profile
from voc.embeddings.manifest import validate_manifest
from voc_ml.embeddings import pin_input
assert resolve_profile().dimensions == 384
"""
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
