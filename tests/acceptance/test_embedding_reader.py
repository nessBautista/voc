"""Opt-in core consumer: clean runtime, full verification, then offline pinned reads.

Only reads existing S3 objects. Set VOC_EMBEDDING_DATASET_VERSION,
VOC_EMBEDDING_RELEASE_ID and VOC_EMBEDDING_MANIFEST_SHA256 from publication output.
"""

import os
import subprocess
import sys

import pytest


@pytest.mark.live
def test_independent_s3_consumer_then_offline_cache(tmp_path):
    values = [
        os.environ.get(name)
        for name in (
            "VOC_EMBEDDING_DATASET_VERSION",
            "VOC_EMBEDDING_RELEASE_ID",
            "VOC_EMBEDDING_MANIFEST_SHA256",
        )
    ]
    if not all(values):
        pytest.skip(
            "Set dataset/release IDs and manifest SHA-256 for an existing S3 release"
        )
    script = """
import importlib.abc, json, sys
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'voc_ml','zenml','mlflow','torch','transformers','sentence_transformers'}:
            raise AssertionError('Unexpected producer/model import: ' + fullname)
sys.meta_path.insert(0, Block())
import numpy as np
from voc import collector, get_embeddings
from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import require_digest, require_uuid
from voc.embeddings.reader import SharedEmbeddingReader
from voc.storage import load_storage
root, dataset_id, release_id, manifest_hash = sys.argv[1:]
require_uuid(dataset_id); require_uuid(release_id); require_digest(manifest_hash)
settings = load_storage(destination='local')
assert settings.runtime_root == Path(root)
assert not settings.runtime_root.exists(), 'Consumer runtime must start empty'
snapshot = collector.get_dataset(stage='prepared', version=dataset_id, source='s3')
bundle = get_embeddings(dataset=snapshot, version=release_id, allow_subset=True)
assert bundle.info['embedding_release_id'] == release_id
assert bundle.info['dataset']['release_id'] == dataset_id
assert bundle.reviews.model_text.tolist() == snapshot.data.model_text.iloc[:len(bundle.reviews)].tolist()
assert bundle.reviews.record_id.tolist() == snapshot.data.record_id.iloc[:len(bundle.reviews)].tolist()
assert len(bundle.reviews) <= 1000, 'This acceptance check targets a bounded release'
manifest_path = next(settings.runtime_root.glob('cache/embeddings/releases/*/*/manifest.json'))
assert file_hash(manifest_path) == manifest_hash
class Offline:
    def get_object(self, **kwargs):
        raise OSError('network disabled for acceptance')
reader = SharedEmbeddingReader(settings, client=Offline())
repeated = reader.read(dataset=snapshot, version=release_id, allow_subset=True)
np.testing.assert_array_equal(bundle.vectors, repeated.vectors)
assert repeated.info == bundle.info
try:
    reader.read(dataset=snapshot, version='latest', allow_subset=True)
except OSError:
    pass
else:
    raise AssertionError('Latest must resolve online')
assert not (settings.runtime_root / 'models').exists()
assert not (settings.runtime_root / 'embeddings/runs').exists()
print(json.dumps({'state': 'verified', 'offline_pinned': True, 'latest_requires_network': True,
                  'manifest_sha256': manifest_hash, 'release': bundle.info}))
"""
    environment = dict(os.environ, VOC_DATA_DIR=str(tmp_path / "consumer"))
    # No identity creation or tracking setup is needed even with production S3 config.
    result = subprocess.run(
        [sys.executable, "-c", script, environment["VOC_DATA_DIR"], *values],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (tmp_path / "reader-verification.json").write_text(result.stdout)
