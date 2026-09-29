"""Validated source configuration and fixed UTC collection windows."""

import os
import re
import tomllib
from datetime import UTC, datetime

from ..paths import data_root


def utc(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Use an explicit timezone")
    return dt.astimezone(UTC).isoformat()


def load_config(path):
    with open(path, "rb") as f:
        config = tomllib.load(f)
    return validate_config(config)


def validate_config(config):
    config = dict(config)
    config.setdefault("raw_store", str(data_root() / "raw" / "raw.db"))
    config.setdefault("collector_dir", str(data_root() / "collector"))
    config.setdefault("seed_path", os.environ.get("VOC_SEED_PATH", ""))
    update = dict(config.get("update", {}))
    update.setdefault("mode", "incremental")
    update.setdefault("overlap_days", 7)
    update.setdefault("max_seconds", 180)
    update.setdefault("max_response_bytes", 2 * 1024 * 1024)
    update.setdefault("max_capture_bytes", 100 * 1024 * 1024)
    if update.get("until", "run_start") == "run_start":
        update["until"] = datetime.now(UTC).isoformat()
        update["until_basis"] = "run_start"
    update["until"] = utc(update["until"])
    if update.get("since"):
        update["since"] = utc(update["since"])
        if update["since"] >= update["until"]:
            raise ValueError("since must precede until")
    if update["mode"] not in ("incremental", "backfill"):
        raise ValueError("Unknown mode")
    if update["mode"] == "backfill" and not update.get("since"):
        raise ValueError("Backfill needs since")
    if type(update["overlap_days"]) is not int or update["overlap_days"] < 0:
        raise ValueError("Invalid overlap")
    for key in ("max_seconds", "max_response_bytes", "max_capture_bytes"):
        if type(update[key]) is not int or update[key] <= 0:
            raise ValueError("Invalid budget: " + key)
    sources = config.get("sources", [])
    if len(sources) != 2 or {s["provider"] for s in sources} != {"apple_rss", "play"}:
        raise ValueError("Configure exactly Apple RSS and Google Play")
    for source in sources:
        for k in ("max_requests", "max_items"):
            if type(source[k]) is not int or source[k] <= 0:
                raise ValueError("Invalid source budget")
        if not source.get("app_id") or not source.get("country"):
            raise ValueError("Missing app/country")
        app = str(source["app_id"])
        if not re.fullmatch(r"[a-z]{2}", source["country"]):
            raise ValueError("Use a two-letter lowercase country")
        if source["provider"] == "apple_rss" and not app.isdigit():
            raise ValueError("Apple app_id must be numeric")
        if source["provider"] == "play":
            if not re.fullmatch(r"[A-Za-z0-9_.]+", app):
                raise ValueError("Invalid Play app_id")
            if not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", source.get("lang", "")):
                raise ValueError("Play requires language")
    config["update"] = update
    return config
