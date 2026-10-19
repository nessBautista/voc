"""Prepare star-labeled reviews and an independent manual-labeling sample."""

import numpy as np
import pandas as pd
from pysentimiento.preprocessing import preprocess_tweet
from sklearn.model_selection import train_test_split

LABEL_BY_RATING = {
    1: "negative",
    2: "negative",
    3: "neutral",
    4: "positive",
    5: "positive",
}
SPLITS = ("train", "test", "validation")
LABELS = ("negative", "neutral", "positive")


def text_keys(texts):
    return texts.map(
        lambda text: " ".join(preprocess_tweet(str(text), lang="es").casefold().split())
    )


def assign_rating_splits(frame, seed=42, train_fraction=0.6, validation_fraction=0.2):
    """Keep all rows, stratify labels and keep equivalent texts in the same split.

    Allocate repeated-text groups first, then fill exact per-class quotas with
    singleton texts. Stop if the quotas cannot accommodate a complete group.
    """
    test_fraction = round(1 - train_fraction - validation_fraction, 12)
    if min(train_fraction, validation_fraction, test_fraction) <= 0:
        raise ValueError(
            "Train, validation and test fractions must be positive and sum to 1."
        )
    rng = np.random.default_rng(seed)
    train, held = train_test_split(
        frame,
        test_size=round(1 - train_fraction, 12),
        stratify=frame.label,
        random_state=seed,
    )
    validation, test = train_test_split(
        held,
        test_size=round(test_fraction / (1 - train_fraction), 12),
        stratify=held.label,
        random_state=seed,
    )
    targets = np.array(
        [
            part.label.value_counts().reindex(LABELS, fill_value=0).to_numpy()
            for part in (train, test, validation)
        ]
    )
    remaining = targets.copy()
    groups = pd.crosstab(frame.text_key, frame.label).reindex(
        columns=LABELS, fill_value=0
    )
    groups = groups.iloc[rng.permutation(len(groups))]
    sizes = groups.sum(axis=1)
    repeated = groups.loc[sizes.gt(1)].sort_values(list(LABELS), ascending=False)
    repeated = repeated.loc[
        repeated.sum(axis=1).sort_values(ascending=False, kind="stable").index
    ]
    assigned = {}
    for key, counts in zip(repeated.index, repeated.to_numpy()):
        fits = (remaining >= counts).all(axis=1)
        if not fits.any():
            raise ValueError(
                "No es posible mantener los grupos completos con estas cuotas."
            )
        priority = ((remaining / targets) * counts).sum(axis=1)
        chosen = int(np.argmax(np.where(fits, priority, -np.inf)))
        remaining[chosen] -= counts
        assigned[key] = SPLITS[chosen]
    singles = groups.loc[sizes.eq(1)]
    for index, label in enumerate(LABELS):
        keys = singles.index[singles[label].eq(1)].to_numpy().copy()
        rng.shuffle(keys)
        names = np.repeat(SPLITS, remaining[:, index])
        if len(keys) != len(names):
            raise ValueError(
                "Las cuotas de partición no coinciden con los textos restantes."
            )
        assigned.update(zip(keys, names))
    result = frame.copy()
    result["split"] = result.text_key.map(assigned)
    assert result.split.notna().all()
    assert result.groupby("text_key").split.nunique().max() == 1
    for i, name in enumerate(SPLITS):
        assert np.array_equal(
            result.loc[result.split.eq(name), "label"]
            .value_counts()
            .reindex(LABELS, fill_value=0),
            targets[i],
        )
    return result


def build_training_frames(dataset, seed=42, social_per_platform=100):
    required = {"record_id", "text", "platform", "rating", "record_date", "app_id"}
    if not required <= set(dataset.columns):
        raise ValueError(f"Faltan columnas: {sorted(required - set(dataset.columns))}")
    if dataset.record_id.isna().any() or dataset.record_id.duplicated().any():
        raise ValueError("Los identificadores deben estar completos y ser únicos.")
    data = dataset.copy()
    if data.text.isna().any() or data.text.str.strip().eq("").any():
        raise ValueError("Se encontraron reseñas sin texto.")
    data["text_key"] = text_keys(data.text)
    # A few emoji-only rows become empty with RoBERTuito's preprocessing.
    data = data.loc[data.text_key.ne("")].copy()
    rated = data.loc[data.rating.notna()].copy()
    if not rated.rating.isin(LABEL_BY_RATING).all():
        raise ValueError("Hay calificaciones fuera del rango entero de 1 a 5.")
    rated["label"] = rated.rating.map(LABEL_BY_RATING)
    rated["label_source"] = "star_rating"
    rated = assign_rating_splits(rated, seed)
    excluded_keys = set(rated.text_key)
    social_parts = []
    for platform in ("x", "youtube"):
        pool = data.loc[
            data.platform.eq(platform)
            & data.rating.isna()
            & ~data.text_key.isin(excluded_keys)
        ].drop_duplicates("text_key")
        if len(pool) < social_per_platform:
            raise ValueError(
                f"No hay {social_per_platform} textos distintos disponibles de {platform}."
            )
        sample = pool.sample(n=social_per_platform, random_state=seed).copy()
        sample["label"] = ""
        sample["label_source"] = "manual_pending"
        excluded_keys.update(sample.text_key)
        social_parts.append(sample)
    social = pd.concat(social_parts, ignore_index=True)
    columns = [
        "record_id",
        "text",
        "label",
        "platform",
        "rating",
        "record_date",
        "app_id",
        "label_source",
    ]
    return rated[columns + ["split"]].reset_index(drop=True), social[
        columns
    ].reset_index(drop=True)
