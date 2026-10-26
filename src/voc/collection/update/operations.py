"""Collect bounded source updates and commit only complete results."""

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from ...paths import raw_path
from ..raw.store import RawStore
from ..shared.config import utc, validate_config
from ..shared.serialization import encoded, now
from .contracts import UpdateResult
from .sources import AppleSource, HTTPTransport, PlaySource


def _storefront_country(row, prefix):
    """Read native locale JSON from the seed or the collector's country-only form."""
    locale = row.get(prefix + "locale")
    if isinstance(locale, str):
        locale = locale.strip()
        if locale.startswith("{"):
            locale = json.loads(locale)
    if isinstance(locale, dict):
        country = locale.get("country")
    elif isinstance(locale, str) and locale:
        country = locale
    else:
        # Older rows can lack provider locale; preserve the canonical seed scope.
        country = row.get("country")
    return country.strip().lower() if isinstance(country, str) else ""


def _boundary(raw, source, update):
    if update.get("since"):
        return update["since"], "explicit"
    scope = ":".join(
        [source["provider"], str(source["app_id"]), source["country"].lower()]
    )
    checkpoint = raw.checkpoint(scope)
    if checkpoint is None:
        frame = raw.read().data
        # Bootstrap from timestamps explicitly belonging to this source, never Appbot dates.
        prefix = source["provider"] + "__"
        dates = []
        for row in frame.to_dict("records"):
            if str(row.get(prefix + "target")) != str(source["app_id"]):
                continue
            if _storefront_country(row, prefix) != source["country"].lower():
                continue
            value = row.get(prefix + "updated_at")
            if isinstance(value, str):
                dates.append(utc(value))
        if not dates:
            raise ValueError(
                "No native-source timestamp; configure update.since explicitly"
            )
        checkpoint = max(dates)
    return (
        datetime.fromisoformat(checkpoint) - timedelta(days=update["overlap_days"])
    ).isoformat(), "overlap"


def update(*, store=None, config, operation_id=None, transport=None):
    """Fetch both stores within budgets; reconcile and commit only on complete collection.

    Inject a transport for offline tests. Production defaults to bounded HTTPS.
    An operation ID permits retrying a completed commit without collecting twice.
    """
    config = validate_config(config)
    raw = RawStore(raw_path(store or config["raw_store"]))
    operation = operation_id or str(uuid.uuid4())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", operation):
        raise ValueError("operation_id must be a simple identifier")
    prior = raw.operation(operation)
    if prior:
        previous = json.loads(prior["report"])
        if config["update"].get("until_basis") == "run_start":
            config["update"]["until"] = previous["config"]["update"]["until"]
        if previous["config"] != config:
            raise ValueError(
                "Operation ID already belongs to a different configuration"
            )
        return UpdateResult(prior["id"], previous)
    parent = raw.revision()["id"]
    run_dir = Path(config["collector_dir"]) / "captures" / operation / str(uuid.uuid4())
    run_dir.mkdir(parents=True, exist_ok=False)
    salt_file = Path(config["collector_dir"]) / "author.salt"
    if not salt_file.exists():
        try:
            with salt_file.open("xb") as f:
                f.write(os.urandom(32))
            salt_file.chmod(0o600)
        except FileExistsError:
            pass
    salt = salt_file.read_bytes()
    limits = config["update"]
    deadline = time.monotonic() + limits["max_seconds"]
    report = {
        "status": "failed",
        "operation_id": operation,
        "parent_revision": parent,
        "started_at": now(),
        "config": config,
        "sources": {},
    }
    observations = []
    checkpoints = {}
    captured = 0
    transport = transport or HTTPTransport()
    for source in config["sources"]:
        provider = source["provider"]
        stats = {
            "status": "failed",
            "requests": 0,
            "observations": 0,
            "stop_reason": None,
        }
        report["sources"][provider] = stats
        try:
            since, basis = _boundary(raw, source, limits)
            if since >= limits["until"]:
                raise ValueError("Source boundary must precede cutoff")
            stats.update(since=since, until=limits["until"], boundary_basis=basis)
            cfg = SimpleNamespace(
                target=str(source["app_id"]),
                country=source["country"].lower(),
                lang=source.get("lang", "es"),
                max_items=source["max_items"],
            )
            adapter = (AppleSource if provider == "apple_rss" else PlaySource)(
                cfg, salt, "v1"
            )
            stats["parser_version"] = adapter.parser_version
            cursor = None
            seen_cursors = set()
            source_records = []
            oldest = None
            for page_number in range(1, source["max_requests"] + 1):
                if time.monotonic() >= deadline:
                    raise RuntimeError("time_limit")
                url, body = adapter.request(cursor)
                response = transport.request(
                    url,
                    body,
                    byte_limit=limits["max_response_bytes"],
                    deadline=deadline,
                )
                stats["requests"] += 1
                if len(response.body) > limits["max_response_bytes"]:
                    raise RuntimeError("response_limit")
                captured += len(response.body)
                if captured > limits["max_capture_bytes"]:
                    raise RuntimeError("capture_limit")
                capture = run_dir / f"{provider}-{page_number:04d}.response"
                with capture.open("xb") as f:
                    f.write(response.body)
                items, next_cursor = adapter.parse(response, cursor)
                records = []
                for item in items:
                    record = adapter.record(item)
                    if (
                        not isinstance(record["source_id"], str)
                        or not record["source_id"]
                    ):
                        raise ValueError("Missing native review ID")
                    if record["rating"] is not None and record["rating"] not in range(
                        1, 6
                    ):
                        raise ValueError("Invalid source rating")
                    record.update(
                        provider=provider,
                        app_id=str(source["app_id"]),
                        country=cfg.country,
                        fetched_at=now(),
                        capture_ref=str(capture),
                        run_id=operation,
                        raw=encoded(item),
                    )
                    records.append(record)
                if provider == "play" and not records and next_cursor is not None:
                    raise ValueError(
                        "Empty Play page with continuation cannot prove completion"
                    )
                stats["observations"] += len(records)
                if stats["observations"] > source["max_items"]:
                    raise RuntimeError("item_limit")
                dates = [r["updated_at"] for r in records]
                if dates:
                    oldest = min(dates + [oldest] if oldest else dates)
                source_records.extend(
                    r
                    for r in records
                    if r["updated_at"] <= limits["until"]
                    and (provider == "apple_rss" or r["updated_at"] >= since)
                )
                # Play stops only after a whole page is older than the overlap boundary.
                boundary = bool(dates) and max(dates) < since
                if provider == "play" and boundary:
                    stats.update(status="success", stop_reason="overlap_boundary")
                    break
                exhausted = next_cursor is None or (provider == "play" and not items)
                if exhausted:
                    if provider == "apple_rss" and (oldest is None or oldest > since):
                        stats["stop_reason"] = "feed_coverage_gap"
                    else:
                        stats.update(
                            status="success",
                            stop_reason="feed_window"
                            if provider == "apple_rss"
                            else "source_exhausted",
                        )
                    break
                if next_cursor in seen_cursors:
                    raise ValueError("Repeated pagination cursor")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                if page_number == source["max_requests"]:
                    stats["stop_reason"] = "request_limit"
                    break
                remaining = deadline - time.monotonic()
                if remaining < adapter.interval:
                    raise RuntimeError("time_limit")
                if isinstance(transport, HTTPTransport):
                    time.sleep(adapter.interval)
            stats["oldest_observation"] = oldest
            if stats["status"] == "success":
                observations.extend(source_records)
                scope = ":".join([provider, str(source["app_id"]), cfg.country])
                checkpoints[scope] = limits["until"]
        except Exception as error:  # noqa: BLE001 -- preserve failed attempt evidence and block commit
            stats.update(
                status="failed", stop_reason=str(error), error_type=type(error).__name__
            )
    report["completed_at"] = now()
    if all(s["status"] == "success" for s in report["sources"].values()):
        report["status"] = "success"
        try:
            rid = raw.commit(
                observations,
                parent=parent,
                operation=operation,
                report=report,
                checkpoints=checkpoints,
            )
            report = json.loads(raw.revision(rid)["report"])
        except Exception as error:  # noqa: BLE001 -- preserve failed attempt evidence and block commit
            report.update(status="failed", commit_error=str(error))
            rid = None
    else:
        rid = None
    (run_dir / "report.json").write_text(encoded(report))
    return UpdateResult(rid, report)
