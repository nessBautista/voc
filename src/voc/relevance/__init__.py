"""Portable linear relevance filter: NumPy only, no pickled estimators."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from voc.datasets.manifest import file_hash


def load_model(path, *, sha256, profile_id):
    path = Path(path)
    if file_hash(path) != sha256:
        raise ValueError("Relevance model checksum mismatch")
    model = json.loads(path.read_bytes())
    if (
        model["schema_version"] != "voc-relevance-linear-v1"
        or model["profile_id"] != profile_id
    ):
        raise ValueError("Incompatible relevance model/profile")
    if model["platforms"] != ["x", "youtube"] or model["classes"] != [0, 1]:
        raise ValueError("Unsupported relevance scope/classes")
    size = model["dimensions"]
    if type(size) is not int or size < 1:
        raise ValueError("Invalid relevance dimensions")
    for key in ("mean", "scale", "coefficients"):
        value = np.asarray(model[key], dtype=float)
        if value.shape != (size,) or not np.isfinite(value).all():
            raise ValueError("Invalid linear model coefficients")
    if (np.asarray(model["scale"]) <= 0).any() or not np.isfinite(model["intercept"]):
        raise ValueError("Invalid linear model scaling/intercept")
    return model


def decisions(reviews, vectors, model):
    """Keep stores; margin > 0 means relevant for social. Scores aren't probabilities."""
    vectors = np.asarray(vectors)
    if (
        vectors.shape != (len(reviews), model["dimensions"])
        or not np.isfinite(vectors).all()
        or not reviews.record_id.is_unique
        or reviews.empty
        or not reviews.platform.isin(["app_store", "google_play", "x", "youtube"]).all()
    ):
        raise ValueError("Invalid aligned relevance inputs")
    social = reviews.platform.isin(model["platforms"]).to_numpy()
    margins = np.full(len(reviews), np.nan)
    x = vectors[social].astype(float)
    margins[social] = ((x - model["mean"]) / model["scale"]) @ np.asarray(
        model["coefficients"]
    ) + model["intercept"]
    keep = ~social | (margins > 0)
    return pd.DataFrame(
        {
            "record_id": reviews.record_id.to_numpy(),
            "platform": reviews.platform.to_numpy(),
            "keep": keep,
            "margin": margins,
            "reason": np.where(
                ~social, "store-pass-through", np.where(keep, "relevant", "irrelevant")
            ),
        }
    )
