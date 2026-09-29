"""Seed numeric versions and collector text versions must survive sharing exactly."""

import json
from dataclasses import replace

import pandas as pd
import pytest
from tests.integration.test_shared_publication import MemoryS3
from tests.integration.test_shared_publication import pair as pair_fixture
from tests.unit.test_storage_bootstrap import storage

from voc.datasets.parquet import ENCODING, decode_scalar
from voc.datasets.publisher import export_frame, publish_release, validate_manifest
from voc.datasets.shared_reader import SharedDatasetReader


@pytest.fixture
def pair(store):
    return pair_fixture.__wrapped__(store)


def test_mixed_scalar_export_preserves_values_types_and_retry_bytes(tmp_path):
    values = [
        1.0,
        "unified-update-v1",
        None,
        True,
        1,
        "1",
        float("nan"),
        pd.NA,
        float("inf"),
    ]
    frame = pd.DataFrame({"schema_version": pd.Series(values, dtype=object)})
    path = tmp_path / "raw.parquet"
    first = export_frame(frame, path)
    assert first["columns"][0]["encoding"] == ENCODING
    assert export_frame(frame, path) == first
    reader = SharedDatasetReader(storage(tmp_path, "local"), client=MemoryS3())
    restored = reader.validate_frame(path, first, "raw")
    pd.testing.assert_frame_equal(restored, frame, check_exact=True)
    for i in range(6):
        assert type(restored.iloc[i, 0]) is type(values[i])
    assert restored.iloc[7, 0] is pd.NA
    assert frame.iloc[0, 0] == 1.0  # Export did not mutate input to JSON strings.


def test_real_release_reader_decodes_mixed_raw_and_keeps_workable_normal(
    tmp_path, pair
):
    raw, workable = pair
    for name in ("apple_rss__schema_version", "play__schema_version"):
        raw.data[name] = pd.Series(
            [1.0, "unified-update-v1"] + [None] * (len(raw.data) - 2), dtype=object
        )
    client = MemoryS3()
    settings = storage(tmp_path, "s3")
    result = publish_release(raw, workable, settings, client=client)
    manifest = json.loads(client.objects[result["manifest_key"]])
    assert manifest["format_version"] == 2
    assert len([c for c in manifest["raw"]["columns"] if "encoding" in c]) == 2
    assert not any("encoding" in c for c in manifest["workable"]["columns"])
    reader = SharedDatasetReader(
        replace(settings, runtime_root=tmp_path / "reader"), client=client
    )
    restored = reader.read(result["release_id"], stage="raw")
    pd.testing.assert_frame_equal(restored.data, raw.data, check_exact=True)
    pd.testing.assert_frame_equal(reader.read().data, workable.data, check_exact=True)
    assert publish_release(raw, workable, settings, client=client) == result
    manifest["format_version"] = 1
    with pytest.raises(ValueError, match="encoding"):
        validate_manifest(manifest, reader.objects)


def test_unknown_encoding_and_unsupported_mixed_values_fail(tmp_path):
    with pytest.raises(ValueError):
        decode_scalar('["execute","anything"]')
    frame = pd.DataFrame({"mixed": [1.0, {"not": "a scalar"}]})
    with pytest.raises(TypeError, match="scalar"):
        export_frame(frame, tmp_path / "raw.parquet")
    assert not (tmp_path / "raw.parquet").exists()
