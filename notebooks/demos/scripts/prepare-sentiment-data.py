"""Build two sentiment CSVs from workflow01's verified local dataset cache."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voc_demo.datasets.manifest import file_hash, json_bytes
from voc_demo.datasets.shared_reader import SharedDatasetReader
from voc_demo.sentiment.training_data import build_training_frames, text_keys
from voc_demo.settings import data_root

RELEASE = "b7a61a8b-27d8-46a7-b2ce-67d97268b741"


def main():
    root = data_root()
    candidates = list((root / "cache/datasets").glob(f"*/{RELEASE}.json"))
    if len(candidates) != 1:
        raise ValueError(
            "Se requiere una única copia local del dataset publicado de workflow01."
        )
    envelope = json.loads(candidates[0].read_bytes())
    if hashlib.sha256(envelope["text"].encode()).hexdigest() != envelope["sha256"]:
        raise ValueError("El manifiesto local no coincide con su checksum.")
    manifest = json.loads(envelope["text"])
    if manifest["release_id"] != RELEASE:
        raise ValueError("El identificador del dataset no coincide.")
    item = manifest["workable"]
    identity = hashlib.sha256(json_bytes([item["key"], item["sha256"]])).hexdigest()
    source = candidates[0].parent / f"{identity}.parquet"
    # Full checksum, schema, row-count and content validation; no AWS access.
    dataset = SharedDatasetReader.validate_frame(None, source, item, "prepared")
    rated, social = build_training_frames(dataset)
    output = root / "sentiment"
    output.mkdir(parents=True, exist_ok=True)
    paths = {"rated_reviews.csv": rated, "social_reviews_to_label.csv": social}
    if any((output / name).exists() for name in paths):
        raise FileExistsError(
            "Los CSV ya existen; no se sobrescriben etiquetas ni particiones."
        )
    for name, frame in paths.items():
        # UTF-8 BOM lets Excel display Spanish correctly; quote multiline reviews.
        frame.to_csv(
            output / name, index=False, encoding="utf-8-sig", lineterminator="\n"
        )
    # Re-read deliverables and check IDs, original text, labels, quotas and disjoint groups.
    import pandas as pd

    saved_rated = pd.read_csv(output / "rated_reviews.csv", keep_default_na=False)
    saved_social = pd.read_csv(
        output / "social_reviews_to_label.csv", keep_default_na=False
    )
    assert saved_rated.record_id.tolist() == rated.record_id.tolist()
    assert saved_rated.text.tolist() == rated.text.tolist()
    assert saved_social.text.tolist() == social.text.tolist()
    assert saved_social.label.eq("").all() and saved_social.rating.eq("").all()
    assert saved_social.platform.value_counts().to_dict() == {"x": 100, "youtube": 100}
    empty = text_keys(dataset.text).eq("")
    assert len(saved_rated) == (dataset.rating.notna() & ~empty).sum()
    keys = text_keys(saved_rated.text)
    assert saved_rated.assign(key=keys).groupby("key").split.nunique().max() == 1
    assert not set(keys) & set(text_keys(saved_social.text))
    summary = {
        "dataset_release_id": RELEASE,
        "dataset_sha256": item["sha256"],
        "seed": 42,
        "rating_to_label": {"1-2": "negative", "3": "neutral", "4-5": "positive"},
        "rated_rows": len(rated),
        "rated_labels": rated.label.value_counts().to_dict(),
        "excluded_empty_after_preprocessing": dataset.loc[
            empty, ["record_id", "platform"]
        ].to_dict(orient="records"),
        "excluded_rated_rows": int((empty & dataset.rating.notna()).sum()),
        "splits": pd.crosstab(rated.split, rated.label).to_dict(orient="index"),
        "social_rows": len(social),
        "social_platforms": social.platform.value_counts().to_dict(),
        "policies": [
            "All rated reviews with nonempty preprocessed text retained; original text preserved.",
            "Equivalent preprocessed texts stay in one split, including conflicting star labels.",
            "Social sample is unfiltered for relevance, unique by normalized text and disjoint from rated texts.",
            "Labels inferred from stars are weak labels; social labels intentionally blank.",
        ],
        "files": {
            name: {"rows": len(frame), "sha256": file_hash(output / name)}
            for name, frame in paths.items()
        },
    }
    (output / "data_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
