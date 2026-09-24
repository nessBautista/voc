"""Explicit runtime paths. No filesystem writes at import time."""

import os
from pathlib import Path


def data_root():
    return Path(os.environ.get("VOC_DATA_DIR", "data/runtime")).resolve()


def raw_path(store=None):
    return Path(store).resolve() if store else data_root() / "raw" / "raw.db"


def publication_path():
    return data_root() / "publications.db"
