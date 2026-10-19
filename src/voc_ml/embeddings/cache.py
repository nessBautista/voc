"""Single-writer SQLite cache: validate and durably commit one complete batch."""

import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np

from voc.embeddings.contracts import canonical_json, encoder_identity, require_digest


def validate_vectors(vectors, rows, dimensions, normalized=True):
    array = np.asarray(vectors)
    if array.dtype.str != "<f4" or array.shape != (rows, dimensions):
        raise ValueError(
            "Expected little-endian float32 vectors with the declared shape"
        )
    for start in range(0, rows, 4096):
        batch = array[start : start + 4096]
        if not np.isfinite(batch).all():
            raise ValueError("Non-finite embedding values")
        if normalized and not np.allclose(
            np.linalg.norm(batch, axis=1), 1.0, atol=1e-4, rtol=0
        ):
            raise ValueError("Expected unit-normalized embeddings")
    return array


def _checksum(encoder_id, input_hash, blob, token_count):
    return hashlib.sha256(
        blob + canonical_json([encoder_id, input_hash, token_count])
    ).hexdigest()


class EmbeddingCache:
    """A corrupt entry fails explicitly; it is never silently overwritten or reused."""

    def __init__(self, path, descriptor, profile):
        self.path = Path(path)
        self.descriptor = json.loads(canonical_json(descriptor))
        self.encoder_id = encoder_identity(self.descriptor)
        if self.descriptor["profile_id"] != profile.profile_id:
            raise ValueError("Cache descriptor/profile mismatch")
        self.profile = profile
        self.db = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported embedding cache schema")
            if version == 0:
                if self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise ValueError(
                        "Refusing to adopt an unversioned existing database"
                    )
                self.db.executescript("""
                    BEGIN;
                    CREATE TABLE encoders (encoder_id TEXT PRIMARY KEY, descriptor TEXT NOT NULL);
                    CREATE TABLE vectors (
                        encoder_id TEXT NOT NULL REFERENCES encoders(encoder_id),
                        input_hash TEXT NOT NULL, vector BLOB NOT NULL,
                        token_count INTEGER NOT NULL, checksum TEXT NOT NULL,
                        PRIMARY KEY (encoder_id, input_hash)
                    );
                    PRAGMA user_version=1;
                    COMMIT;
                """)
            encoded = canonical_json(self.descriptor).decode()
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO encoders VALUES (?, ?)",
                    (self.encoder_id, encoded),
                )
                saved = self.db.execute(
                    "SELECT descriptor FROM encoders WHERE encoder_id=?",
                    (self.encoder_id,),
                ).fetchone()[0]
                if saved != encoded:
                    raise ValueError("Cache encoder descriptor mismatch")
        except BaseException:
            self.db.close()
            raise
        return self

    def __exit__(self, *args):
        self.db.close()

    def get(self, input_hash):
        require_digest(input_hash, "input_hash")
        row = self.db.execute(
            "SELECT vector, token_count, checksum FROM vectors WHERE encoder_id=? AND input_hash=?",
            (self.encoder_id, input_hash),
        ).fetchone()
        if row is None:
            return None
        blob, tokens, checksum = row
        if (
            not isinstance(blob, bytes)
            or len(blob) != self.profile.dimensions * 4
            or type(tokens) is not int
            or tokens < 1
            or checksum != _checksum(self.encoder_id, input_hash, blob, tokens)
        ):
            raise ValueError("Corrupt cached vector or token count; refusing reuse")
        vector = np.frombuffer(blob, dtype="<f4")
        validate_vectors(
            vector.reshape(1, -1), 1, self.profile.dimensions, self.profile.normalize
        )
        return vector.copy(), tokens

    def put_batch(self, hashes, vectors, token_counts):
        array = validate_vectors(
            vectors, len(hashes), self.profile.dimensions, self.profile.normalize
        )
        if (
            not hashes
            or len(set(hashes)) != len(hashes)
            or len(token_counts) != len(hashes)
        ):
            raise ValueError(
                "A cache batch requires unique hashes and matching token counts"
            )
        rows = []
        for key, vector, tokens in zip(hashes, array, token_counts, strict=True):
            require_digest(key, "input_hash")
            if type(tokens) is not int or tokens < 1:
                raise ValueError(
                    "token_count must include special tokens and be positive"
                )
            blob = vector.tobytes()
            rows.append(
                (
                    self.encoder_id,
                    key,
                    blob,
                    tokens,
                    _checksum(self.encoder_id, key, blob, tokens),
                )
            )
        with self.db:
            for row in rows:
                self.db.execute(
                    "INSERT OR IGNORE INTO vectors VALUES (?, ?, ?, ?, ?)", row
                )
                saved = self.db.execute(
                    "SELECT vector, token_count, checksum FROM vectors WHERE encoder_id=? AND input_hash=?",
                    row[:2],
                ).fetchone()
                if saved != row[2:]:
                    raise ValueError("Existing cache entry differs; refusing overwrite")
