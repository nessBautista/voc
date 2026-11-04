"""Explicit model download/checking; inference remains offline by default."""

import json
from pathlib import Path

from voc.paths import data_root

from .robertuito import LABEL_MAP, MODEL


def prepare_model(config, *, download=False):
    from huggingface_hub import snapshot_download

    config.__post_init__()
    directory = data_root() / "models/sentiment"
    path = Path(
        snapshot_download(
            config.model,
            revision=config.revision,
            cache_dir=str(directory),
            local_files_only=not download,
            token=bool(download and config.model != MODEL),
            allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
        )
    )
    if path.name != config.revision:
        raise ValueError("Downloaded checkpoint revision differs")
    metadata = json.loads((path / "config.json").read_bytes())
    if metadata["model_type"] != "roberta" or metadata["id2label"] != {
        "0": "NEG",
        "1": "NEU",
        "2": "POS",
    }:
        raise ValueError("Unexpected model architecture or labels")
    for required in ("model.safetensors", "tokenizer_config.json"):
        if not (path / required).is_file():
            raise ValueError(f"Checkpoint is incomplete: {required}")
    return {
        "state": "model_files_verified",
        "model": config.model,
        "revision": config.revision,
        "directory": str(path),
        "labels": LABEL_MAP,
        "predictions": 0,
    }
