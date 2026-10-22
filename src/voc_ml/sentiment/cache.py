"""Transactional cache of validated model predictions, independent of review IDs."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from voc.analysis.contracts import SENTIMENT_LABELS, ReviewSentiment
from voc.embeddings.contracts import canonical_json, fingerprint, require_digest


@dataclass(frozen=True)
class Prediction:
    model_label: str
    probabilities: dict[str, float]
    token_count: int

    def validate(self, input_sha256):
        # Reuse consumer validation so cache and published results agree.
        ReviewSentiment(
            "cache-entry",
            "classified",
            self.model_label,
            self.probabilities,
            input_sha256=input_sha256,
        )
        if type(self.token_count) is not int or self.token_count < 1:
            raise ValueError("token_count must include special tokens and be positive")

    def as_dict(self):
        return {
            "model_label": self.model_label,
            "probabilities": self.probabilities,
            "token_count": self.token_count,
        }


def prediction(probabilities, token_count):
    """Select the model argmax without rounding or altering its probabilities."""
    if not isinstance(probabilities, dict) or set(probabilities) != set(
        SENTIMENT_LABELS
    ):
        raise ValueError("Expected exactly three sentiment probabilities")
    result = Prediction(
        max(SENTIMENT_LABELS, key=probabilities.get), dict(probabilities), token_count
    )
    result.validate("0" * 64)
    return result


class PredictionCache:
    def __init__(self, path, descriptor):
        self.path = Path(path)
        self.descriptor = json.loads(canonical_json(descriptor))
        self.classifier_id = fingerprint(self.descriptor)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            schema = self.db.execute("PRAGMA user_version").fetchone()[0]
            if schema not in (0, 1):
                raise ValueError("Unsupported sentiment cache schema")
            if schema == 0:
                if self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise ValueError("Refusing to adopt an unversioned database")
                self.db.executescript("""
                    BEGIN;
                    CREATE TABLE classifiers (id TEXT PRIMARY KEY, descriptor TEXT NOT NULL);
                    CREATE TABLE predictions (
                        classifier_id TEXT NOT NULL REFERENCES classifiers(id),
                        input_hash TEXT NOT NULL, payload TEXT NOT NULL, checksum TEXT NOT NULL,
                        PRIMARY KEY(classifier_id, input_hash));
                    PRAGMA user_version=1;
                    COMMIT;
                """)
            encoded = canonical_json(self.descriptor).decode()
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO classifiers VALUES (?, ?)",
                    (self.classifier_id, encoded),
                )
                if (
                    self.db.execute(
                        "SELECT descriptor FROM classifiers WHERE id=?",
                        (self.classifier_id,),
                    ).fetchone()[0]
                    != encoded
                ):
                    raise ValueError("Cached classifier descriptor mismatch")
        except BaseException:
            self.db.close()
            raise
        return self

    def __exit__(self, *args):
        self.db.close()

    def get(self, input_hash):
        require_digest(input_hash, "input_hash")
        row = self.db.execute(
            "SELECT payload, checksum FROM predictions WHERE classifier_id=? AND input_hash=?",
            (self.classifier_id, input_hash),
        ).fetchone()
        if row is None:
            return None
        payload, checksum = row
        if checksum != fingerprint([self.classifier_id, input_hash, payload]):
            raise ValueError("Corrupt sentiment cache checksum; refusing reuse")
        try:
            result = Prediction(**json.loads(payload))
            result.validate(input_hash)
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("Invalid cached sentiment prediction") from error
        return result

    def put_batch(self, entries):
        """Validate the entire batch before inserting. Conflicts roll back all rows."""
        if not entries or len({key for key, _ in entries}) != len(entries):
            raise ValueError("A prediction batch requires unique input hashes")
        rows = []
        for key, result in entries:
            require_digest(key, "input_hash")
            result.validate(key)
            payload = canonical_json(result.as_dict()).decode()
            rows.append(
                (
                    self.classifier_id,
                    key,
                    payload,
                    fingerprint([self.classifier_id, key, payload]),
                )
            )
        with self.db:
            for row in rows:
                self.db.execute(
                    "INSERT OR IGNORE INTO predictions VALUES (?, ?, ?, ?)", row
                )
                saved = self.db.execute(
                    "SELECT payload, checksum FROM predictions WHERE classifier_id=? AND input_hash=?",
                    row[:2],
                ).fetchone()
                if saved != row[2:]:
                    raise ValueError(
                        "Existing sentiment prediction differs; refusing overwrite"
                    )
