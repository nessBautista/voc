"""SQLite raw revisions: unchanged payloads are shared across committed revisions."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from voc.models import Snapshot
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def now():
    return datetime.now(UTC).isoformat()


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class RawStore:
    """Explicitly opened store. Reads resolve a revision once and reconstruct its rows."""

    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def connect(self):
        if not self.path.is_file():
            raise FileNotFoundError(
                "Raw store not initialized; run voc-ml dataset init."
            )
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    def initialize(self, frame, seed_identity):
        if self.path.exists():
            raise FileExistsError(
                "Raw store already exists; initialization never overwrites it."
            )
        if (
            "record_id" not in frame
            or frame.record_id.isna().any()
            or not frame.record_id.is_unique
        ):
            raise ValueError("Seed requires unique, non-null record_id values.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive reservation avoids racing another initializer.
        self.path.touch(exist_ok=False)
        try:
            with self.connect() as c:
                c.executescript("""
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE revisions(seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                    parent TEXT, operation TEXT UNIQUE NOT NULL, committed_at TEXT NOT NULL,
                    report TEXT NOT NULL);
                CREATE TABLE versions(record_id TEXT NOT NULL, seq INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(record_id,seq),
                    FOREIGN KEY(seq) REFERENCES revisions(seq));
                CREATE TABLE identities(provider TEXT, app TEXT, native_id TEXT, record_id TEXT,
                    PRIMARY KEY(provider,app,native_id));
                CREATE TABLE checkpoints(scope TEXT PRIMARY KEY, value TEXT NOT NULL, revision TEXT NOT NULL);
                """)
                rid = str(uuid.uuid4())
                c.execute(
                    "INSERT INTO metadata VALUES('columns',?)",
                    (encoded(list(frame.columns)),),
                )
                c.execute(
                    "INSERT INTO metadata VALUES('seed',?)", (encoded(seed_identity),)
                )
                c.execute(
                    "INSERT INTO revisions VALUES(1,?,NULL,'seed',?,?)",
                    (
                        rid,
                        now(),
                        encoded(
                            {
                                "status": "success",
                                "seed": seed_identity,
                                "rows": len(frame),
                            }
                        ),
                    ),
                )
                for row in json.loads(
                    frame.to_json(orient="records", force_ascii=False)
                ):
                    c.execute(
                        "INSERT INTO versions VALUES(?,?,?)",
                        (str(row["record_id"]), 1, encoded(row)),
                    )
                    for provider in ("apple_rss", "play"):
                        native = row.get(provider + "__source_id")
                        if native is not None:
                            app = row.get(provider + "__target") or row.get("app_id")
                            c.execute(
                                "INSERT INTO identities VALUES(?,?,?,?)",
                                (
                                    provider,
                                    str(app),
                                    str(native),
                                    str(row["record_id"]),
                                ),
                            )
            return rid
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise

    def revision(self, revision="latest"):
        with self.connect() as c:
            r = c.execute(
                "SELECT * FROM revisions ORDER BY seq DESC LIMIT 1"
                if revision == "latest"
                else "SELECT * FROM revisions WHERE id=?",
                () if revision == "latest" else (revision,),
            ).fetchone()
            if r is None:
                raise KeyError("Unknown committed raw revision")
            return dict(r)

    def operation(self, operation):
        with self.connect() as c:
            r = c.execute(
                "SELECT * FROM revisions WHERE operation=?", (operation,)
            ).fetchone()
            return dict(r) if r else None

    def read(self, revision="latest"):
        rev = self.revision(revision)
        with self.connect() as c:
            columns = json.loads(
                c.execute("SELECT value FROM metadata WHERE key='columns'").fetchone()[
                    0
                ]
            )
            rows = c.execute(
                """SELECT v.payload FROM versions v JOIN
              (SELECT record_id,MAX(seq) seq FROM versions WHERE seq<=? GROUP BY record_id) x
              ON v.record_id=x.record_id AND v.seq=x.seq ORDER BY v.record_id""",
                (rev["seq"],),
            )
            frame = pd.DataFrame([json.loads(r[0]) for r in rows], columns=columns)
        return Snapshot(
            frame,
            {
                "revision_id": rev["id"],
                "raw_store_id": str(self.path),
                "parent": rev["parent"],
                "row_count": len(frame),
                "committed_at": rev["committed_at"],
            },
        )

    def checkpoint(self, scope):
        with self.connect() as c:
            r = c.execute(
                "SELECT value FROM checkpoints WHERE scope=?", (scope,)
            ).fetchone()
            return r[0] if r else None

    def commit(self, observations, *, parent, operation, report, checkpoints):
        """Commit only complete two-store updates. Serialized parent check prevents lost updates."""
        if report.get("status") != "success" or set(report.get("sources", {})) != {
            "apple_rss",
            "play",
        }:
            raise ValueError("Both stores must have successful reports before commit")
        if any(v.get("status") != "success" for v in report["sources"].values()):
            raise ValueError("Partial or gap collection cannot commit")
        report = json.loads(encoded(report))
        report["observations_sha256"] = digest(observations)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute(
                "SELECT id,parent,report FROM revisions WHERE operation=?", (operation,)
            ).fetchone()
            if existing:
                if (
                    existing["parent"] != parent
                    or json.loads(existing["report"]).get("observations_sha256")
                    != report["observations_sha256"]
                ):
                    raise ValueError("Operation ID already committed different inputs")
                return existing[0]
            latest = c.execute(
                "SELECT seq,id FROM revisions ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            if latest["id"] != parent:
                raise RuntimeError(
                    "Raw revision changed during collection; retry against its current revision"
                )
            seq, rid = latest["seq"] + 1, str(uuid.uuid4())
            columns = json.loads(
                c.execute("SELECT value FROM metadata WHERE key='columns'").fetchone()[
                    0
                ]
            )
            c.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                (seq, rid, parent, operation, now(), encoded(report)),
            )
            counts = {"inserted": 0, "edited": 0, "duplicates": 0, "stale": 0}
            for obs in observations:
                provider, app, native = (
                    obs["provider"],
                    str(obs["app_id"]),
                    str(obs["source_id"]),
                )
                if provider not in ("play", "apple_rss") or not obs["source_id"]:
                    raise ValueError("Invalid observation identity")
                source_counts = report["sources"][provider].setdefault(
                    "counts", dict.fromkeys(counts, 0)
                )
                identity = c.execute(
                    "SELECT record_id FROM identities WHERE provider=? AND app=? AND native_id=?",
                    (provider, app, native),
                ).fetchone()
                record_id = identity[0] if identity else f"{provider}:{app}:{native}"
                old = c.execute(
                    "SELECT payload FROM versions WHERE record_id=? ORDER BY seq DESC LIMIT 1",
                    (record_id,),
                ).fetchone()
                row = json.loads(old[0]) if old else dict.fromkeys(columns)
                previous_date = row.get(provider + "__updated_at")
                if old and previous_date:
                    from .config import utc

                    if utc(obs["updated_at"]) < utc(previous_date):
                        counts["stale"] += 1
                        source_counts["stale"] += 1
                        continue
                # Compare semantic content/version fields, not repeated fetch/capture metadata.
                changed = (
                    not old
                    or any(
                        row.get(provider + "__" + k) != obs.get(k)
                        for k in ("text", "title", "rating", "app_version")
                    )
                    or (
                        bool(previous_date)
                        and utc(previous_date) != utc(obs["updated_at"])
                    )
                )
                if not changed:
                    counts["duplicates"] += 1
                    source_counts["duplicates"] += 1
                    continue
                row.update(
                    record_id=record_id,
                    platform="app_store" if provider == "apple_rss" else "google_play",
                    app_id=app,
                    country=obs["country"].upper(),
                    text=obs.get("text"),
                    title=obs.get("title"),
                    rating=obs.get("rating"),
                    updated_at=obs["updated_at"],
                    record_date=obs["updated_at"],
                    date_basis="source_updated_at",
                    date_precision="second",
                    date_timezone="UTC",
                    collected_at=obs["fetched_at"],
                    preferred_provider=provider,
                )
                if not old:
                    row.update(
                        language=None,
                        language_basis="requested lang=es; not detected"
                        if provider == "play"
                        else "MX storefront; language unknown",
                        relevance="app_target_match",
                        identity_basis="provider_identity",
                        source_count=1,
                        source_record_ids_json=encoded([record_id]),
                    )
                fields = dict(
                    obs,
                    target=app,
                    provider_record_id=native,
                    source="appstore" if provider == "apple_rss" else "playstore",
                    locale=obs["country"],
                    schema_version="unified-update-v1",
                    collected_via="newest",
                )
                for k, v in fields.items():
                    if provider + "__" + k in columns:
                        row[provider + "__" + k] = v
                row = {k: row.get(k) for k in columns}
                c.execute(
                    "INSERT INTO versions VALUES(?,?,?) ON CONFLICT(record_id,seq) DO UPDATE SET payload=excluded.payload",
                    (record_id, seq, encoded(row)),
                )
                c.execute(
                    "INSERT OR IGNORE INTO identities VALUES(?,?,?,?)",
                    (provider, app, native, record_id),
                )
                counts["edited" if old else "inserted"] += 1
                source_counts["edited" if old else "inserted"] += 1
            report = dict(report, counts=counts)
            c.execute(
                "UPDATE revisions SET report=? WHERE id=?", (encoded(report), rid)
            )
            for scope, value in checkpoints.items():
                # Historical backfills must not move monitoring checkpoints backwards.
                c.execute(
                    """INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET
                value=MAX(checkpoints.value,excluded.value),revision=CASE WHEN excluded.value>=checkpoints.value
                THEN excluded.revision ELSE checkpoints.revision END""",
                    (scope, value, rid),
                )
            return rid
