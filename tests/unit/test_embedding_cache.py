"""Durable per-input reuse, corruption checks and batch transaction boundaries."""

import sqlite3

import numpy as np
import pytest

from voc.embeddings.contracts import text_hash
from voc.embeddings.profiles import resolve_profile
from voc_ml.embeddings.cache import EmbeddingCache, validate_vectors


def descriptor():
    return {
        "profile_id": resolve_profile().profile_id,
        "adapter": "fake-test-v1",
        "device": "cpu",
        "dtype": "<f4",
        "software": {
            "torch": "test",
            "transformers": "test",
            "sentence-transformers": "test",
        },
    }


def matrix(n):
    vectors = np.zeros((n, 384), dtype="<f4")
    vectors[:, 0] = 1
    return vectors


def test_committed_vectors_and_token_counts_survive_reopen(tmp_path):
    path = tmp_path / "cache.db"
    key = text_hash("input")
    with EmbeddingCache(path, descriptor(), resolve_profile()) as cache:
        assert cache.get(key) is None
        cache.put_batch([key], matrix(1), [200])
    with EmbeddingCache(path, descriptor(), resolve_profile()) as cache:
        vector, tokens = cache.get(key)
        np.testing.assert_array_equal(vector, matrix(1)[0])
        assert tokens == 200
        cache.put_batch([key], matrix(1), [200])  # Exact repeat is harmless.
        with pytest.raises(ValueError, match="refusing overwrite"):
            cache.put_batch([key], matrix(1), [201])
        assert cache.get(key)[1] == 200


@pytest.mark.parametrize(
    "column,value", [("vector", b"broken"), ("token_count", 2), ("checksum", "bad")]
)
def test_corrupt_cache_fails_without_repair(tmp_path, column, value):
    path = tmp_path / "cache.db"
    key = text_hash("input")
    with EmbeddingCache(path, descriptor(), resolve_profile()) as cache:
        cache.put_batch([key], matrix(1), [20])
        with cache.db:
            cache.db.execute(f"UPDATE vectors SET {column}=?", (value,))
        with pytest.raises(ValueError, match="Corrupt"):
            cache.get(key)


def test_bad_descriptor_or_schema_rejected(tmp_path):
    path = tmp_path / "cache.db"
    with EmbeddingCache(path, descriptor(), resolve_profile()) as cache, cache.db:
        cache.db.execute("UPDATE encoders SET descriptor='{}'")
    with (
        pytest.raises(ValueError, match="descriptor mismatch"),
        EmbeddingCache(path, descriptor(), resolve_profile()),
    ):
        pass
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with (
        pytest.raises(ValueError, match="Unsupported"),
        EmbeddingCache(path, descriptor(), resolve_profile()),
    ):
        pass


def test_transaction_failure_rolls_back_entire_batch(tmp_path):
    with EmbeddingCache(
        tmp_path / "cache.db", descriptor(), resolve_profile()
    ) as cache:
        keys = [text_hash("a"), text_hash("b")]
        # Trigger aborts the second INSERT after the first has executed.
        cache.db.execute(
            f"CREATE TRIGGER fail_second BEFORE INSERT ON vectors WHEN NEW.input_hash='{keys[1]}' BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            cache.put_batch(keys, matrix(2), [3, 4])
        assert cache.get(keys[0]) is None
        assert cache.get(keys[1]) is None


@pytest.mark.parametrize(
    "bad",
    [
        np.zeros((2, 384), dtype="float32"),
        np.ones((2, 383), dtype="float32"),
        matrix(2).astype("float64"),
        np.full((2, 384), np.nan, dtype="float32"),
        np.full((2, 384), np.inf, dtype="float32"),
    ],
)
def test_invalid_vectors_never_commit(tmp_path, bad):
    with EmbeddingCache(
        tmp_path / "cache.db", descriptor(), resolve_profile()
    ) as cache:
        with pytest.raises(ValueError):
            cache.put_batch([text_hash("a"), text_hash("b")], bad, [3, 4])
        assert cache.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 0


@pytest.mark.parametrize("tokens", [[True], [-1], [None], []])
def test_invalid_token_counts_never_commit(tmp_path, tokens):
    with (
        EmbeddingCache(tmp_path / "cache.db", descriptor(), resolve_profile()) as cache,
        pytest.raises(ValueError),
    ):
        cache.put_batch([text_hash("a")], matrix(1), tokens)


def test_on_disk_dtype_is_not_coerced():
    with pytest.raises(ValueError):
        validate_vectors(matrix(1).astype(">f4"), 1, 384)
