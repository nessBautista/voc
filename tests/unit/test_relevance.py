"""Reconstructed training contracts and portable prediction equivalence."""

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from voc.datasets.manifest import file_hash
from voc.relevance import decisions, load_model
from voc_ml.relevance import preparation, training


@pytest.fixture
def annotated(tmp_path, monkeypatch):
    n = 40
    frame = pd.DataFrame(
        {
            "record_id": [f"r{i}" for i in range(n)],
            "text": [f"review {i}" for i in range(n)],
            "model_text": [f"review {i}" for i in range(n)],
            "platform": ["x"] * 20 + ["youtube"] * 20,
        }
    )
    rng = np.random.default_rng(42)
    x = rng.normal(size=(n, 4)).astype("float32")
    y = np.arange(n) % 2
    x[:, 0] += 4 * y - 2
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    bundle = SimpleNamespace(
        reviews=frame,
        vectors=x,
        info={"profile": {"profile_id": "a" * 64}, "embedding_release_id": "test"},
    )
    snapshot = SimpleNamespace(data=frame, info={"release_id": "test"})
    monkeypatch.setattr(training, "load_local_config", lambda _: {})
    monkeypatch.setattr(training, "read_local_inputs", lambda _: (snapshot, bundle))
    labels = frame.drop(columns="model_text").assign(
        label=np.where(y, "relevant", "irrelevant"),
        annotation_origin="synthetic-test",
        annotator="test",
    )
    path = tmp_path / "annotations.csv"
    labels.to_csv(path, index=False)
    return path, labels, bundle


def test_training_roundtrip_and_fold_accounting(annotated, tmp_path):
    path, labels, bundle = annotated
    target = tmp_path / "model"
    result = training.train("unused", path, target)
    assert result["portable_roundtrip_verified"]
    assert result["rows_trained"] == 40
    saved = pd.read_csv(target / "validation.csv")
    assert saved.record_id.tolist() == labels.record_id.tolist()
    assert saved.fold.value_counts().to_dict() == dict.fromkeys(range(5), 8)
    assert sum(map(sum, result["confusion_matrix"])) == 40
    model = load_model(
        target / "model.json", sha256=result["model_sha256"], profile_id="a" * 64
    )
    rows = bundle.reviews.copy()
    rows.loc[:1, "platform"] = ["app_store", "google_play"]
    out = decisions(rows, bundle.vectors, model)
    assert out.loc[:1, "keep"].all()
    assert out.loc[:1, "margin"].isna().all()
    assert out.record_id.tolist() == rows.record_id.tolist()
    with pytest.raises(ValueError, match="profile"):
        load_model(
            target / "model.json", sha256=result["model_sha256"], profile_id="b" * 64
        )
    (target / "model.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        load_model(
            target / "model.json", sha256=result["model_sha256"], profile_id="a" * 64
        )


@pytest.mark.parametrize(
    "mutation",
    ["blank-label", "missing-author", "wrong-text", "missing-stratum", "duplicate-id"],
)
def test_invalid_annotation_never_creates_model(annotated, tmp_path, mutation):
    path, labels, _ = annotated
    if mutation == "blank-label":
        labels.loc[0, "label"] = ""
    elif mutation == "missing-author":
        labels.loc[0, "annotator"] = " "
    elif mutation == "wrong-text":
        labels.loc[0, "text"] = "another text"
    elif mutation == "missing-stratum":
        labels = labels.loc[labels.platform.eq("x")]
    else:
        labels.loc[0, "record_id"] = labels.loc[1, "record_id"]
    labels.to_csv(path, index=False)
    with pytest.raises(ValueError):
        training.train("unused", path, tmp_path / "model")
    assert not (tmp_path / "model").exists()


def test_duplicate_prepared_text_is_deduplicated_before_cv(annotated, tmp_path):
    path, _, bundle = annotated
    bundle.reviews.loc[2, "model_text"] = bundle.reviews.loc[0, "model_text"]
    result = training.train("unused", path, tmp_path / "model")
    assert result["duplicates_removed"] == 1
    validation = pd.read_csv(tmp_path / "model/validation.csv")
    assert validation.model_text.is_unique


def test_conflicting_labels_fail(annotated, tmp_path):
    path, _, bundle = annotated
    bundle.reviews.loc[1, "model_text"] = bundle.reviews.loc[0, "model_text"]
    with pytest.raises(ValueError, match="Conflicting"):
        training.train("unused", path, tmp_path / "model")


def test_saved_notebook_recovery_checks_text_and_never_executes(tmp_path, monkeypatch):
    frame = pd.DataFrame(
        {
            "record_id": [f"r{i}" for i in range(100)],
            "platform": "x",
            "text": [f"saved text {i}" for i in range(100)],
        }
    )
    labels = dict.fromkeys(range(100), "relevant")
    output = "\n".join(f"[{i}]\n{row.text}\n{'-' * 100}" for i, row in frame.iterrows())
    source = {
        "cells": [
            {
                "cell_type": "code",
                "source": [
                    f"etiquetas_lote_x_1 = {labels!r}\nraise RuntimeError('must never execute')"
                ],
            },
            {
                "cell_type": "code",
                "source": ["lote_x = not_executable"],
                "outputs": [{"text": output}],
            },
        ]
    }
    notebook = tmp_path / "saved.ipynb"
    notebook.write_text(json.dumps(source))
    monkeypatch.setattr(preparation, "SOURCE_SHA256", file_hash(notebook))
    recovered = preparation.recover_x(notebook, frame)
    assert recovered.record_id.tolist() == frame.record_id.tolist()
    frame.loc[0, "text"] = "changed"
    with pytest.raises(ValueError, match="index/text"):
        preparation.recover_x(notebook, frame)
    notebook.write_text("{}")
    with pytest.raises(ValueError, match="source"):
        preparation.recover_x(notebook, frame)


def test_proposals_require_explicit_review_and_preserve_ids(tmp_path):
    original = pd.DataFrame(
        {
            "record_id": ["a", "b"],
            "platform": ["x", "youtube"],
            "text": ["x text", "youtube text"],
            "label": ["relevant", ""],
            "annotator": ["historical", ""],
            "annotation_origin": ["recovered", "pending"],
            "source_sha256": ["", ""],
        }
    )
    annotations = tmp_path / "annotations.csv"
    original.to_csv(annotations, index=False)
    review = original.iloc[[1]].assign(
        proposed_label="irrelevant",
        human_label="",
        human_reviewer="",
        review_status="pending",
    )
    path = tmp_path / "review.csv"
    review.to_csv(path, index=False)
    with pytest.raises(ValueError, match="explicit"):
        preparation.import_review(annotations, path, tmp_path / "final.csv")
    review = review.assign(
        human_label="uncertain",
        human_reviewer="test reviewer",
        review_status="reviewed",
    )
    review.to_csv(path, index=False)
    preparation.import_review(annotations, path, tmp_path / "final.csv")
    result = pd.read_csv(tmp_path / "final.csv")
    assert result.label.tolist() == ["relevant", "uncertain"]
    assert result.record_id.tolist() == ["a", "b"]
    review.loc[1, "text"] = "modified"
    review.to_csv(path, index=False)
    with pytest.raises(ValueError, match="text/platform"):
        preparation.import_review(annotations, path, tmp_path / "another.csv")
