import pandas as pd

from voc_demo.sentiment.adaptation import prepare_splits
from voc_demo.sentiment.training_data import (
    LABEL_BY_RATING,
    build_training_frames,
    text_keys,
)


def source():
    rows = [
        {
            "record_id": f"store-{rating}-{i}",
            "text": f"Reseña de tienda grupo {rating} caso {i}",
            "rating": rating,
            "platform": "google_play",
            "app_id": "app",
            "record_date": "2026-01-01",
        }
        for rating in range(1, 6)
        for i in range(12)
    ]
    # Preserve repeated texts with different ratings under the user's label rule.
    for row in rows[:3]:
        row["text"] = "El mismo texto de una reseña"
    rows[2]["rating"] = 5
    rows += [
        {
            "record_id": f"{platform}-{i}",
            "text": f"Mensaje de {platform} sobre aplicación caso {i}",
            "rating": None,
            "platform": platform,
            "app_id": "app",
            "record_date": "2026-01-01",
        }
        for platform in ("x", "youtube")
        for i in range(110)
    ]
    return pd.DataFrame(rows)


def test_rating_rule_keeps_all_rows_and_exact_split_sizes_without_text_leakage():
    original = source()
    rated, social = build_training_frames(original)
    assert len(rated) == 60
    assert rated.split.value_counts().to_dict() == {
        "train": 36,
        "test": 12,
        "validation": 12,
    }
    assert rated.label.tolist() == rated.rating.map(LABEL_BY_RATING).tolist()
    assert rated.text.tolist() == original.loc[original.rating.notna(), "text"].tolist()
    assert (
        rated.assign(key=text_keys(rated.text)).groupby("key").split.nunique().max()
        == 1
    )
    # Notebook keeps the authored partitions and conflicting weak labels intact.
    assert sum(len(part) for part in prepare_splits(rated).values()) == 60
    assert not set(text_keys(rated.text)) & set(text_keys(social.text))


def test_manual_sample_is_reproducible_unrated_unique_and_unlabeled():
    _, social = build_training_frames(source())
    _, second = build_training_frames(source())
    assert social.equals(second)
    assert social.platform.value_counts().to_dict() == {"x": 100, "youtube": 100}
    assert social.rating.isna().all()
    assert social.label.eq("").all()
    assert text_keys(social.text).is_unique


def test_emoji_only_rows_empty_after_preprocessing_are_excluded():
    frame = source()
    frame.loc[0, "text"] = "❤️❤️❤️"
    rated, _ = build_training_frames(frame)
    assert "store-1-0" not in set(rated.record_id)
