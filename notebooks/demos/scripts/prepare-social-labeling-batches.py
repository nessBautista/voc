"""Create two additional 100-X/100-YouTube manual-labeling batches, locally."""

import csv
import hashlib
import json
import sys
from io import StringIO
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voc_demo.datasets.manifest import file_hash, json_bytes
from voc_demo.datasets.shared_reader import SharedDatasetReader
from voc_demo.sentiment.training_data import text_keys
from voc_demo.settings import data_root

RELEASE = "b7a61a8b-27d8-46a7-b2ce-67d97268b741"


def main():
    root = data_root()
    output = root / "sentiment"
    targets = [
        output / f"social_reviews_to_label_block{number:02d}.csv" for number in (2, 3)
    ]
    if any(path.exists() for path in targets):
        raise FileExistsError("Los bloques ya existen; no se sobrescriben etiquetas.")
    manifests = list((root / "cache/datasets").glob(f"*/{RELEASE}.json"))
    if len(manifests) != 1:
        raise ValueError(
            "Se requiere la copia local verificada del dataset de workflow01."
        )
    envelope = json.loads(manifests[0].read_bytes())
    if hashlib.sha256(envelope["text"].encode()).hexdigest() != envelope["sha256"]:
        raise ValueError("Checksum del manifiesto inválido.")
    manifest = json.loads(envelope["text"])
    if manifest["release_id"] != RELEASE:
        raise ValueError("El identificador del dataset no coincide.")
    item = manifest["workable"]
    identity = hashlib.sha256(json_bytes([item["key"], item["sha256"]])).hexdigest()
    dataset = SharedDatasetReader.validate_frame(
        None, manifests[0].parent / f"{identity}.parquet", item, "prepared"
    )

    excluded_ids, excluded_keys, originals = set(), set(), {}
    for path in output.iterdir():
        if path.suffix.lower() != ".csv":
            continue
        originals[path.name] = file_hash(path)
        content = path.read_text(encoding="utf-8-sig")
        reader = csv.reader(StringIO(content))
        first, second = next(reader, []), next(reader, [])
        title = len(first) == 1 and {"record_id", "text"} <= set(second)
        frame = pd.read_csv(
            StringIO(content), dtype=str, keep_default_na=False, skiprows=int(title)
        )
        if not {"record_id", "text"} <= set(frame):
            raise ValueError(
                f"No se pueden verificar identificadores y textos de {path.name}."
            )
        excluded_ids.update(frame.record_id)
        excluded_keys.update(text_keys(frame.text))

    pool = dataset.loc[
        dataset.platform.isin(["youtube", "x"]) & dataset.rating.isna()
    ].copy()
    pool["text_key"] = text_keys(pool.text)
    pool = pool.loc[
        pool.text_key.ne("")
        & ~pool.record_id.isin(excluded_ids)
        & ~pool.text_key.isin(excluded_keys)
    ]
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
    batches, selected_keys, selected_ids = [], set(excluded_keys), set(excluded_ids)
    for number, path in zip((2, 3), targets):
        parts = []
        for platform in ("youtube", "x"):
            eligible = pool.loc[
                pool.platform.eq(platform) & ~pool.text_key.isin(selected_keys)
            ].drop_duplicates("text_key")
            if len(eligible) < 100:
                raise ValueError(f"No hay suficientes reseñas nuevas de {platform}.")
            part = eligible.sample(n=100, random_state=42 + number).copy()
            part["label"] = ""
            part["label_source"] = "manual_pending"
            selected_keys.update(part.text_key)
            parts.append(part)
        batches.append((path, pd.concat(parts, ignore_index=True)[columns]))

    summary = {
        "dataset_release_id": RELEASE,
        "dataset_sha256": item["sha256"],
        "excluded_source_files": originals,
        "files": {},
    }
    for path, batch in batches:
        batch.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
        saved = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert saved.record_id.tolist() == batch.record_id.tolist()
        assert saved.text.tolist() == batch.text.tolist()
        assert saved.platform.value_counts().to_dict() == {"youtube": 100, "x": 100}
        assert saved.label.eq("").all() and saved.rating.eq("").all()
        assert saved.record_id.is_unique and not selected_ids.intersection(
            saved.record_id
        )
        selected_ids.update(saved.record_id)
        summary["files"][path.name] = {
            "rows": len(saved),
            "platforms": saved.platform.value_counts().to_dict(),
            "sha256": file_hash(path),
        }
    all_new = pd.concat([batch for _, batch in batches], ignore_index=True)
    new_keys = text_keys(all_new.text)
    assert new_keys.is_unique and not excluded_keys.intersection(new_keys)
    assert all(file_hash(output / name) == digest for name, digest in originals.items())
    (output / "social_blocks_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
