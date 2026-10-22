"""Immutable local analysis candidates, usable without any ML runtime.

A writer builds and validates a private staging directory before making it
visible. Readers verify every declared artifact and open DuckDB read-only.
Checksums detect corruption, not malicious replacements of both data and hashes.
"""

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd

from voc.analysis.contracts import (
    AnalysisManifest,
    Keyword,
    ReviewAssignment,
    ReviewSentiment,
    TopicRecord,
    validate_input_identity,
    validate_results,
)
from voc.datasets.manifest import file_hash, frame_hash
from voc.embeddings.contracts import canonical_json, require_key, require_uuid
from voc.paths import data_root

from .interpretations import read_records, validate_records, verify_artifacts


@dataclass(frozen=True)
class StoredAnalysis:
    reviews: pd.DataFrame
    assignments: tuple[ReviewAssignment, ...]
    topics: tuple[TopicRecord, ...]
    sentiments: tuple[ReviewSentiment, ...]
    manifest: AnalysisManifest
    interpretations: tuple[dict, ...] = ()


def _root():
    return data_root() / "analyses"


def _read_roots():
    """Optional completed-candidate catalogs; writers still use VOC_DATA_DIR."""
    roots = [_root()]
    for value in os.environ.get("VOC_ANALYSIS_READ_DIRS", "").split(os.pathsep):
        if value:
            path = Path(value)
            if not path.is_absolute():
                raise ValueError("Additional analysis catalogs must be absolute paths")
            if path not in roots:
                roots.append(path)
    for root in roots:
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError("Analysis root must not traverse symlinks")
    return roots


def analysis_directory(analysis_id):
    require_uuid(analysis_id, "analysis_id")
    matches = []
    for root in _read_roots():
        path = root / analysis_id
        if path.is_symlink():
            raise ValueError("Analysis directory must not be a symlink")
        if path.exists():
            matches.append(path)
    if len(matches) > 1:
        raise ValueError("Analysis ID exists in multiple catalogs")
    return matches[0] if matches else _root() / analysis_id


_folder = analysis_directory


def _file(directory, relative):
    require_key(relative)
    path = directory
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Analysis artifacts must not be symlinks")
    if not path.is_file():
        raise ValueError(f"Missing analysis artifact: {relative}")
    return path


def _artifact(path, relative):
    return {
        "path": relative,
        "size_bytes": path.stat().st_size,
        "sha256": file_hash(path),
    }


def _validate_reviews(reviews, inputs):
    validate_input_identity(inputs)
    if not isinstance(reviews, pd.DataFrame) or reviews.empty:
        raise ValueError("Analysis reviews must be a nonempty DataFrame")
    columns = reviews.columns.tolist()
    if any(not isinstance(c, str) or not c for c in columns) or len(
        {c.casefold() for c in columns}
    ) != len(columns):
        raise ValueError("Review column names must be unique, including case")
    if not {"record_id", "model_text", "platform", "record_date", "vector_row"} <= set(
        columns
    ):
        raise ValueError("Missing analysis review columns")
    if reviews.record_id.tolist() != inputs["selection"]["record_ids"]:
        raise ValueError("Review IDs/order differ from pinned selection")
    if reviews.vector_row.tolist() != inputs["selection"]["positions"]:
        raise ValueError("Review vector_row must preserve selected order")
    if frame_hash(reviews) != inputs["reviews_sha256"]:
        raise ValueError("Review content differs from pinned input checksum")


def _metadata(manifest):
    # Exclude file hashes to avoid making the database checksum self-referential.
    return {k: v for k, v in manifest.as_dict().items() if k != "artifacts"}


def _stored_metadata(payload):
    # Prototype candidates stored an empty corrections list beside the manifest.
    value = json.loads(payload)
    if value.get("corrections", []) in ([], ()):
        value.pop("corrections", None)
    return value


def _connect(path, *, read_only):
    # Consumers may query local tables, but cannot use SQL to access other files
    # or automatically install/load extensions. This is not a general SQL API.
    return duckdb.connect(
        str(path),
        read_only=read_only,
        config={
            "enable_external_access": False,
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
        },
    )


def _write_database(
    path, reviews, assignments, topics, sentiments, manifest, interpretations
):
    with _connect(path, read_only=False) as con:
        # One transaction avoids committing each review separately.
        con.execute("BEGIN TRANSACTION")
        con.register("input_reviews", reviews)
        con.execute("CREATE TABLE reviews AS SELECT * FROM input_reviews")
        con.unregister("input_reviews")
        con.execute("CREATE UNIQUE INDEX review_ids ON reviews(record_id)")
        con.execute(
            "CREATE TABLE assignments (record_id VARCHAR PRIMARY KEY, topic_id INTEGER, membership_strength DOUBLE)"
        )
        con.executemany(
            "INSERT INTO assignments VALUES (?, ?, ?)",
            [(a.record_id, a.topic_id, a.membership_strength) for a in assignments],
        )
        con.execute(
            "CREATE TABLE topics (topic_id INTEGER PRIMARY KEY, keywords VARCHAR, evidence_ids VARCHAR, name VARCHAR, description VARCHAR)"
        )
        con.executemany(
            "INSERT INTO topics VALUES (?, ?, ?, ?, ?)",
            [
                (
                    t.topic_id,
                    canonical_json(
                        [{"term": k.term, "weight": k.weight} for k in t.keywords]
                    ).decode(),
                    canonical_json(t.evidence_ids).decode(),
                    t.name,
                    t.description,
                )
                for t in topics
            ],
        )
        con.execute(
            "CREATE TABLE sentiment (record_id VARCHAR PRIMARY KEY, status VARCHAR, model_label VARCHAR, negative DOUBLE, neutral DOUBLE, positive DOUBLE, reason VARCHAR, input_sha256 VARCHAR)"
        )
        con.executemany(
            "INSERT INTO sentiment VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    s.record_id,
                    s.status,
                    s.model_label,
                    *(
                        (s.probabilities or {}).get(k)
                        for k in ("negative", "neutral", "positive")
                    ),
                    s.reason,
                    s.input_sha256,
                )
                for s in sentiments
            ],
        )
        if manifest.schema_version != "voc-analysis-v1":
            con.execute(
                "CREATE TABLE interpretations (topic_id INTEGER PRIMARY KEY, payload VARCHAR NOT NULL)"
            )
            if interpretations:
                con.executemany(
                    "INSERT INTO interpretations VALUES (?, ?)",
                    [
                        (r["topic_id"], canonical_json(r).decode())
                        for r in interpretations
                    ],
                )
        con.execute("CREATE TABLE metadata (json VARCHAR)")
        con.execute(
            "INSERT INTO metadata VALUES (?)",
            [canonical_json(_metadata(manifest)).decode()],
        )
        # The effective views keep one shape for consumers; the model label is the
        # label, since human corrections are not part of this release.
        con.execute(
            "CREATE VIEW effective_assignments AS SELECT *, topic_id AS model_topic_id FROM assignments"
        )
        con.execute(
            "CREATE VIEW effective_sentiment AS SELECT *, model_label AS label FROM sentiment"
        )
        _read_database(con, manifest)  # Validate the actual persisted joins/values.
        con.execute("COMMIT")
        con.execute("CHECKPOINT")
    # The connection is closed before the caller calculates its artifact hash.


def _read_database(con, manifest):
    reviews = con.execute("SELECT * FROM reviews ORDER BY vector_row").df()
    _validate_reviews(reviews, manifest.inputs)
    assignments = tuple(
        ReviewAssignment(*row)
        for row in con.execute(
            "SELECT a.record_id, a.topic_id, a.membership_strength FROM assignments a JOIN reviews r USING(record_id) ORDER BY r.vector_row"
        ).fetchall()
    )
    # Count separately so a join cannot silently hide an unknown record ID.
    if con.execute("SELECT count(*) FROM assignments").fetchone()[0] != len(
        assignments
    ):
        raise ValueError("Assignments contain unknown review IDs")
    topics = tuple(
        TopicRecord(
            row[0],
            tuple(Keyword(**k) for k in json.loads(row[1])),
            tuple(json.loads(row[2])),
            row[3],
            row[4],
        )
        for row in con.execute(
            "SELECT topic_id, keywords, evidence_ids, name, description FROM topics ORDER BY topic_id"
        ).fetchall()
    )
    sentiments = []
    for rid, status, label, neg, neu, pos, reason, digest in con.execute(
        "SELECT s.record_id, s.status, s.model_label, s.negative, s.neutral, s.positive, s.reason, s.input_sha256 FROM sentiment s JOIN reviews r USING(record_id) ORDER BY r.vector_row"
    ).fetchall():
        probabilities = (
            None
            if (neg, neu, pos) == (None, None, None)
            else dict(zip(("negative", "neutral", "positive"), (neg, neu, pos)))
        )
        sentiments.append(
            ReviewSentiment(rid, status, label, probabilities, reason, digest)
        )
    if con.execute("SELECT count(*) FROM sentiment").fetchone()[0] != len(sentiments):
        raise ValueError("Sentiment contains unknown review IDs")
    if (
        validate_results(reviews.record_id.tolist(), assignments, topics, sentiments)
        != manifest.coverage
    ):
        raise ValueError("Stored coverage differs from manifest")
    metadata = con.execute("SELECT json FROM metadata").fetchall()
    if len(metadata) != 1 or _stored_metadata(metadata[0][0]) != _metadata(manifest):
        raise ValueError("Database metadata differs from manifest")
    interpretations = read_records(con, manifest)
    validate_records(interpretations, assignments)
    return StoredAnalysis(
        reviews, assignments, topics, tuple(sentiments), manifest, interpretations
    )


def write_analysis(
    reviews,
    assignments,
    topics,
    sentiments,
    *,
    inputs,
    configuration,
    code_sha256,
    software,
    provenance,
    analysis_id=None,
    parent_analysis_id=None,
    reports=None,
    interpretations=None,
):
    """Save a new candidate and return its manifest. Never overwrite an ID.

    ``reviews`` is the selected input DataFrame, unchanged. Inputs reference the
    published embedding release, so no vectors are copied here. ``reports`` maps
    safe relative filenames to nonempty bytes stored beneath artifacts/.
    """
    analysis_id = str(uuid4()) if analysis_id is None else analysis_id
    destination = _folder(analysis_id)
    _validate_reviews(reviews, inputs)
    coverage = validate_results(
        reviews.record_id.tolist(), assignments, topics, sentiments
    )
    validate_records(interpretations or (), assignments)
    reports = {} if reports is None else dict(reports)
    for name, value in reports.items():
        require_key(name)
        if not isinstance(value, bytes) or not value:
            raise ValueError("Reports must contain nonempty bytes")
    # Validate caller metadata before writing. Replace this temporary descriptor
    # with the real database hash once the file has been closed.
    info = {
        "schema_version": "voc-analysis-v2"
        if interpretations is not None
        else "voc-analysis-v1",
        "analysis_id": analysis_id,
        "inputs": inputs,
        "configuration": configuration,
        "code_sha256": code_sha256,
        "software": software,
        "provenance": provenance,
        "coverage": coverage,
        "parent_analysis_id": parent_analysis_id,
        "created_at": datetime.now(UTC).isoformat(),
        "artifacts": {
            "database": {"path": "analysis.duckdb", "size_bytes": 1, "sha256": "0" * 64}
        },
    }
    manifest = AnalysisManifest(**info)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive per-ID reservation serializes writers, including competing
    # processes. A process killed mid-write may leave a hidden lock/staging dir,
    # neither of which readers treat as a completed candidate. Use a new run ID.
    lock = destination.parent / f".{analysis_id}.lock"
    with lock.open("x"):
        staging = None
        try:
            if os.path.lexists(destination):
                raise FileExistsError(f"Analysis already exists: {analysis_id}")
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".staging-{analysis_id}-", dir=destination.parent
                )
            )
            (staging / "artifacts").mkdir()
            _write_database(
                staging / "analysis.duckdb",
                reviews,
                assignments,
                topics,
                sentiments,
                manifest,
                interpretations or (),
            )
            artifacts = {
                "database": _artifact(staging / "analysis.duckdb", "analysis.duckdb")
            }
            for name, value in reports.items():
                relative = "artifacts/" + name
                path = staging / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)
                artifacts[relative] = _artifact(path, relative)
            info["artifacts"] = artifacts
            manifest = AnalysisManifest(**info)
            (staging / "manifest.json").write_bytes(
                canonical_json(manifest.as_dict()) + b"\n"
            )
            _load_manifest(staging, analysis_id)
            verify_artifacts(interpretations or (), manifest, staging)
            if os.path.lexists(destination):
                raise FileExistsError(f"Analysis already exists: {analysis_id}")
            staging.rename(destination)  # Same filesystem: readers see all or none.
            return manifest
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
            lock.unlink()


def _load_manifest(directory, analysis_id):
    try:
        manifest = AnalysisManifest.from_dict(
            json.loads(_file(directory, "manifest.json").read_bytes())
        )
    except (TypeError, KeyError) as error:
        raise ValueError("Malformed analysis manifest") from error
    if manifest.analysis_id != analysis_id:
        raise ValueError("Analysis directory and manifest IDs differ")
    database = manifest.artifacts.get("database", {})
    if database.get("path") != "analysis.duckdb":
        raise ValueError("Manifest must declare analysis.duckdb")
    for artifact in manifest.artifacts.values():
        relative = artifact["path"]
        if relative != "analysis.duckdb" and not relative.startswith("artifacts/"):
            raise ValueError("Supporting artifacts must live under artifacts/")
        path = _file(directory, relative)
        if _artifact(path, relative) != artifact:
            raise ValueError(f"Analysis artifact checksum/size mismatch: {relative}")
    return manifest


def read_manifest(analysis_id):
    """Read a completed candidate, verifying all declared artifact bytes."""
    directory = _folder(analysis_id)
    if not directory.is_dir():
        raise LookupError(f"No completed analysis: {analysis_id}")
    return _load_manifest(directory, analysis_id)


@contextmanager
def open_analysis(analysis_id):
    """Yield (read-only connection, verified manifest) for shared query functions.

    Callers must not retain the connection after this context exits. No producer
    state, model files, source dataset or embedding matrix is needed for reads.
    """
    manifest = read_manifest(analysis_id)
    with _connect(
        _folder(manifest.analysis_id) / "analysis.duckdb", read_only=True
    ) as con:
        yield con, manifest


def read_analysis(analysis_id):
    """Load stored results and revalidate their coverage, joins and metadata."""
    with open_analysis(analysis_id) as (con, manifest):
        result = _read_database(con, manifest)
        verify_artifacts(
            result.interpretations, manifest, _folder(manifest.analysis_id)
        )
        return result


def list_analyses():
    """List verified completed manifests, newest first.

    Hidden staging directories and directories without a completion manifest
    are ignored. Corrupt completed candidates raise an error rather than being
    silently reported as valid or missing.
    """
    items = []
    for root in _read_roots():
        if not root.exists():
            continue
        for path in root.iterdir():
            if path.name.startswith(".") or not path.is_dir():
                continue
            if not (path / "manifest.json").exists():
                continue
            items.append(read_manifest(path.name))
    return sorted(
        items,
        key=lambda m: (datetime.fromisoformat(m.created_at), m.analysis_id),
        reverse=True,
    )
