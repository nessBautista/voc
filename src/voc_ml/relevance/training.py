"""Reconstruct the notebook's scaler + balanced linear SVM with fold provenance."""

import json
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from voc.analysis.local_inputs import load_local_config, read_local_inputs
from voc.datasets.manifest import file_hash
from voc.relevance import decisions, load_model


def train(local_inputs, annotations, output):
    from sklearn.metrics import (
        accuracy_score,
        balanced_accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )
    from sklearn.model_selection import StratifiedKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import LinearSVC

    snapshot, bundle = read_local_inputs(load_local_config(local_inputs))
    labels = pd.read_csv(annotations, keep_default_na=False, dtype=str)
    required = {
        "record_id",
        "platform",
        "text",
        "label",
        "annotation_origin",
        "annotator",
    }
    if (
        not required <= set(labels)
        or labels.empty
        or not labels.record_id.is_unique
        or not labels.label.isin(["relevant", "irrelevant", "uncertain"]).all()
        or labels.annotator.str.strip().eq("").any()
        or labels.annotation_origin.str.strip().eq("").any()
    ):
        raise ValueError(
            "Complete labels, unique IDs and annotation authorship are required"
        )
    corpus = snapshot.data.set_index("record_id")
    if not labels.record_id.isin(corpus.index).all():
        raise ValueError("Annotation ID absent from corpus")
    matched = corpus.loc[labels.record_id]
    if (
        labels.platform.tolist() != matched.platform.tolist()
        or labels.text.tolist() != matched.text.tolist()
        or not labels.platform.isin(["x", "youtube"]).all()
    ):
        raise ValueError("Annotation text/platform differs from source")
    # Exact duplicate prepared inputs never appear in both training and validation.
    labels["model_text"] = matched.model_text.to_numpy()
    clear = labels.loc[labels.label.ne("uncertain")].copy()
    if (clear.groupby("model_text").label.nunique() > 1).any():
        raise ValueError("Conflicting labels for identical model text")
    clean = clear.drop_duplicates("model_text").reset_index(drop=True)
    strata = clean.platform + ":" + clean.label
    expected = {
        p + ":" + c for p in ["x", "youtube"] for c in ["relevant", "irrelevant"]
    }
    if set(strata) != expected or strata.value_counts().min() < 5:
        raise ValueError(
            "Need at least 5 distinct clear texts in each platform/class stratum"
        )
    positions = (
        pd.Series(range(len(bundle.reviews)), index=bundle.reviews.record_id)
        .loc[clean.record_id]
        .to_numpy()
    )
    x = bundle.vectors[positions].astype("float64")
    y = clean.label.eq("relevant").astype(int).to_numpy()
    predictions, folds, margins = (
        np.zeros(len(y), int),
        np.zeros(len(y), int),
        np.zeros(len(y)),
    )

    def estimator():
        return make_pipeline(
            StandardScaler(),
            LinearSVC(class_weight="balanced", random_state=42, max_iter=10000),
        )

    baseline = np.zeros(len(y), dtype=int)
    for fold, (training, validation) in enumerate(
        StratifiedKFold(5, shuffle=True, random_state=42).split(x, strata)
    ):
        fitted = estimator().fit(x[training], y[training])
        predictions[validation] = fitted.predict(x[validation])
        margins[validation] = fitted.decision_function(x[validation])
        folds[validation] = fold
        for platform in ("x", "youtube"):
            train_mask = clean.iloc[training].platform.eq(platform).to_numpy()
            valid_mask = clean.iloc[validation].platform.eq(platform).to_numpy()
            baseline[validation[valid_mask]] = int(y[training[train_mask]].mean() > 0.5)
    fitted = estimator().fit(x, y)
    scaler, svm = fitted.steps[0][1], fitted.steps[1][1]
    report = {
        "schema_version": "voc-relevance-training-v1",
        "annotation_sha256": file_hash(annotations),
        "dataset_release_id": snapshot.info["release_id"],
        "embedding_release_id": bundle.info["embedding_release_id"],
        "rows_annotated": len(labels),
        "uncertain_excluded": len(labels) - len(clear),
        "duplicates_removed": len(clear) - len(clean),
        "rows_trained": len(clean),
        "validation": "five-fold-platform-class-stratified-seed42-oof-v1",
        "estimator": {
            "scaler": "StandardScaler",
            "classifier": "LinearSVC",
            "C": 1.0,
            "class_weight": "balanced",
            "random_state": 42,
            "max_iter": 10000,
            "threshold": 0.0,
        },
        "duplicate_policy": "exact-model-text-v1",
        "computation_dtype": "float64",
        "near_duplicate_screening": False,
        "accuracy": accuracy_score(y, predictions),
        "balanced_accuracy": balanced_accuracy_score(y, predictions),
        "precision_relevant": precision_score(y, predictions, zero_division=0),
        "recall_relevant": recall_score(y, predictions, zero_division=0),
        "f1_relevant": f1_score(y, predictions, zero_division=0),
        "confusion_matrix": confusion_matrix(y, predictions, labels=[0, 1]).tolist(),
        "software": {
            name: version(name) for name in ["scikit-learn", "numpy", "pandas"]
        },
        "historical_result_reproduction": False,
    }
    report["per_platform"] = {
        platform: {
            "rows": int((clean.platform == platform).sum()),
            "confusion_matrix": confusion_matrix(
                y[clean.platform == platform],
                predictions[clean.platform == platform],
                labels=[0, 1],
            ).tolist(),
            "balanced_accuracy": balanced_accuracy_score(
                y[clean.platform == platform], predictions[clean.platform == platform]
            ),
        }
        for platform in ("x", "youtube")
    }
    report["platform_majority_control"] = {
        "policy": "majority-per-platform-learned-inside-each-fold",
        "accuracy": accuracy_score(y, baseline),
        "balanced_accuracy": balanced_accuracy_score(y, baseline),
        "confusion_matrix": confusion_matrix(y, baseline, labels=[0, 1]).tolist(),
    }
    model = {
        "schema_version": "voc-relevance-linear-v1",
        "profile_id": bundle.info["profile"]["profile_id"],
        "dimensions": x.shape[1],
        "platforms": ["x", "youtube"],
        "classes": svm.classes_.tolist(),
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "coefficients": svm.coef_[0].tolist(),
        "intercept": float(svm.intercept_[0]),
        "training_record_ids": clean.record_id.tolist(),
        "training_model_texts_sha256": [],
        "training": report,
    }
    from voc.embeddings.contracts import text_hash

    model["training_model_texts_sha256"] = [text_hash(t) for t in clean.model_text]
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    path = target / "model.json"
    path.write_text(json.dumps(model, indent=2) + "\n")
    restored = load_model(path, sha256=file_hash(path), profile_id=model["profile_id"])
    restored_scores = decisions(clean, x, restored).margin.to_numpy()
    np.testing.assert_allclose(
        restored_scores, fitted.decision_function(x), rtol=1e-9, atol=1e-9
    )
    if not np.array_equal(restored_scores > 0, fitted.predict(x).astype(bool)):
        raise ValueError("Portable prediction roundtrip differs")
    clean.assign(
        fold=folds,
        target=y,
        prediction=predictions,
        margin=margins,
        platform_control=baseline,
    ).to_csv(target / "validation.csv", index=False)
    report["model_sha256"] = file_hash(path)
    report["portable_roundtrip_verified"] = True
    (target / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
