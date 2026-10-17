"""prototypeV0 cache seeding and explicit checkpoint actions; no model calls."""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from voc_demo.datasets.demos import (
    PROTOTYPE_FILES,
    DemoSnapshots,
    atomic_bytes,
    folder_lock,
)
from voc_demo.datasets.manifest import json_bytes
from voc_demo.settings import load_settings

FILES = PROTOTYPE_FILES["prototypeV0"]
SOURCE = "demo-source.json"


def check_sample(folder, workable):
    """A legacy sample must still contain the exact rows in the loaded release."""
    sample = pd.read_parquet(folder / "sample.parquet")
    if not sample["record_id"].is_unique or not workable["record_id"].is_unique:
        raise ValueError("Demo review IDs must be unique")
    try:
        expected = (
            workable.set_index("record_id").loc[sample["record_id"]].reset_index()
        )
        pd.testing.assert_frame_equal(
            sample.reset_index(drop=True),
            expected[sample.columns],
            check_dtype=False,
        )
    except (KeyError, AssertionError) as error:
        raise ValueError(
            "Cached demo sample differs from the loaded dataset; use a separate cache"
        ) from error
    return sample


def validate_checkpoint(folder, workable, release_id):
    """Reject partial or stale stage files before sharing a checkpoint.

    Existing stage manifests remain the notebook's authority for parameters.
    These checks verify the links between files without loading any ML model.
    """
    for name in FILES:
        if not (folder / name).is_file() or (folder / name).is_symlink():
            raise ValueError(
                "Complete the notebook first; missing snapshot file: " + name
            )
    sample = check_sample(folder, workable)
    sample = sample.loc[sample["model_text"].fillna("").str.strip().ne("")]
    inputs_hash = hashlib.sha256("".join(sample["text_hash"]).encode()).hexdigest()
    meta = {
        name: json.loads((folder / name).read_text())
        for name in FILES
        if name.endswith(".json")
    }
    for key in ("embeddings.json", "sentiment.json"):
        if meta[key]["inputs_sha256"] != inputs_hash:
            raise ValueError("Stale stage input fingerprint: " + key)
    vectors = np.load(folder / "embeddings.npy", allow_pickle=False)
    with np.load(folder / "reduced.npz", allow_pickle=False) as arrays:
        reduced = arrays["cluster_5d"]
        display = arrays["display_2d"]
    with np.load(folder / "clusters.npz", allow_pickle=False) as arrays:
        labels, probabilities = arrays["labels"], arrays["probabilities"]
    if any(
        len(a) != len(sample) or not np.isfinite(a).all()
        for a in (vectors, reduced, display, labels, probabilities)
    ):
        raise ValueError("Demo arrays do not match the sample")
    if (
        meta["reduced.json"]["embeddings_sha256"]
        != hashlib.sha256(vectors.tobytes()).hexdigest()
    ):
        raise ValueError("Stale dimensionality reduction")
    if (
        meta["clusters.json"]["reduced_sha256"]
        != hashlib.sha256(reduced.tobytes()).hexdigest()
    ):
        raise ValueError("Stale cluster assignments")
    if meta["representations.json"]["manifest"]["inputs_sha256"] != inputs_hash:
        raise ValueError("Stale topic representations")
    sentiment = pd.read_parquet(folder / "sentiment.parquet")
    if sentiment["record_id"].tolist() != sample["record_id"].tolist():
        raise ValueError("Sentiment rows differ from the sample")
    for attempt in meta["labels.json"].get("attempts", {}).values():
        if attempt["request"]["dataset_release_id"] != release_id:
            raise ValueError("LLM answers belong to another dataset release")
    return {
        name: (
            value.get("manifest", value)
            if name != "labels.json"
            else {key: value[key] for key in ("model", "prompt_version")}
        )
        for name, value in meta.items()
    }


def _release_guard(folder, release_id):
    path = folder / SOURCE
    if (
        path.exists()
        and json.loads(path.read_text())["dataset_release_id"] != release_id
    ):
        raise ValueError(
            "Demo cache belongs to another dataset release; select a separate cache directory"
        )


def seed_prototype_cache(
    cache,
    *,
    release_id,
    workable,
    version="latest",
    storage=None,
    client=None,
):
    """Seed an empty demo workspace from S3, never bundled or replacement data.

    A complete working cache is preserved. Partial, unlabelled or differently
    pinned caches stop with an error instead of mixing files or rerunning models.
    """
    cache = Path(cache)
    storage = storage or load_settings()
    if storage.dataset_source != "s3":
        raise ValueError("The demo requires a shared S3 dataset")
    with folder_lock(cache.parent / ".demo-seed-lock"):
        cache.mkdir(parents=True, exist_ok=True)
        _release_guard(cache, release_id)
        if any(cache.iterdir()):
            if not (cache / SOURCE).exists():
                raise ValueError("Unlabelled demo cache; select a new empty SAMPLE_DIR")
            provenance = json.loads((cache / SOURCE).read_text())
            if version != "latest" and provenance.get("snapshot_id") != version:
                raise ValueError(
                    "Cache belongs to another demo snapshot; select a new empty SAMPLE_DIR"
                )
            validate_checkpoint(cache, workable, release_id)
            return {
                "source": "existing cache",
                "files": 0,
                "message": "Existing stage files preserved",
            }

        # Missing credentials, network errors and mismatches must stop the demo.
        source = DemoSnapshots("prototypeV0", storage, client=client).fetch(
            version,
            release_id=release_id,
        )
        origin = "S3 demo " + source.name
        with tempfile.TemporaryDirectory(dir=cache.parent) as temporary:
            staged = Path(temporary) / "seed"
            staged.mkdir()
            for name in FILES:
                shutil.copyfile(source / name, staged / name)
            validate_checkpoint(staged, workable, release_id)
            atomic_bytes(
                staged / SOURCE,
                json_bytes(
                    {
                        "dataset_release_id": release_id,
                        "source": origin,
                        "snapshot_id": source.name,
                    }
                ),
            )
            if any(cache.iterdir()):
                raise ValueError("Cache changed during seeding; no files replaced")
            cache.rmdir()
            staged.rename(cache)
        return {"source": origin, "files": len(FILES), "message": ""}


def publish_prototype_demo(cache, *, release_id, workable, storage=None, client=None):
    """Freeze and validate the complete current run before any S3 upload."""
    cache = Path(cache)
    _release_guard(cache, release_id)
    with tempfile.TemporaryDirectory() as temporary:
        frozen = Path(temporary)
        for name in FILES:
            if not (cache / name).is_file() or (cache / name).is_symlink():
                raise ValueError(
                    "Complete the notebook first; missing snapshot file: " + name
                )
            shutil.copyfile(cache / name, frozen / name)
        keys = validate_checkpoint(frozen, workable, release_id)
        return DemoSnapshots("prototypeV0", storage, client=client).publish(
            frozen,
            release_id=release_id,
            keys=keys,
        )
