"""Checked local step artifacts. JSON references carry hashes, never model pickles."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from voc.analysis import Keyword, ReviewAssignment, ReviewSentiment, TopicRecord
from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import canonical_json
from voc_ml.topics.bertopic import TopicDiscovery

from .inputs import AnalysisInput


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_json(value))
    return {"path": str(path), "sha256": file_hash(path)}


def read_json(reference):
    path = checked_path(reference)
    return json.loads(path.read_bytes())


def checked_path(reference):
    path = Path(reference["path"])
    if path.is_symlink() or file_hash(path) != reference["sha256"]:
        raise ValueError("Workflow artifact checksum mismatch")
    return path


def write_input(pinned, directory):
    pinned.validate()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    pinned.reviews.to_parquet(directory / "reviews.parquet", index=False)
    np.save(directory / "vectors.npy", pinned.vectors, allow_pickle=False)
    files = {
        name: {"path": str(directory / name), "sha256": file_hash(directory / name)}
        for name in ("reviews.parquet", "vectors.npy")
    }
    reference = write_json(
        directory / "input.json", {"info": pinned.info, "files": files}
    )
    read_input(reference)
    return reference


def read_input(reference):
    record = read_json(reference)
    result = AnalysisInput(
        pd.read_parquet(checked_path(record["files"]["reviews.parquet"])),
        np.load(checked_path(record["files"]["vectors.npy"]), allow_pickle=False),
        record["info"],
    )
    result.validate()
    return result


def read_topics(reference, inputs):
    value = read_json(reference)
    result = TopicDiscovery(
        tuple(ReviewAssignment(**row) for row in value["assignments"]),
        tuple(
            TopicRecord(
                **(
                    row
                    | {
                        "keywords": tuple(Keyword(**k) for k in row["keywords"]),
                        "evidence_ids": tuple(row["evidence_ids"]),
                    }
                )
            )
            for row in value["topics"]
        ),
        value["report"],
    )
    result.validate(inputs)
    return result


def read_sentiments(reference, record_ids):
    value = read_json(reference)
    records = tuple(ReviewSentiment(**row) for row in value["sentiments"])
    if value["report"]["state"] != "completed" or [
        row.record_id for row in records
    ] != list(record_ids):
        raise ValueError("Sentiment output must cover the selected reviews in order")
    return records, value
