import uuid
from dataclasses import replace
import pandas as pd
from voc.models import StorageSettings
from voc.datasets.manifest import json_bytes, file_hash, frame_hash
from voc.datasets.shared_reader import SharedDatasetReader
from tests.support import MemoryS3

def test_empty_reader_and_pinned_cache(tmp_path):
    settings = StorageSettings("local", "s3", tmp_path / "consumer", "team-test-bucket", "us-east-2", "reader", "voc/datasets", "members", None)
    ids = [str(uuid.uuid4()) for _ in range(4)]
    release, raw_id, artifact_id, run_id = ids
    frame = pd.DataFrame({"record_id": ["one"], "text": ["synthetic review"]})
    file = tmp_path / "rows.parquet"
    frame.to_parquet(file, index=False)
    common = {"row_count": 1, "size_bytes": file.stat().st_size, "sha256": file_hash(file), "columns": [{"name": n, "arrow_type": "string", "pandas_dtype": "object"} for n in frame]}
    manifest = {"format_version": 1, "release_id": release, "publisher_id": "fixture", "created_at": "2026-09-28T00:00:00+00:00", "raw": common | {"revision_id": raw_id, "key": f"voc/datasets/raw/{raw_id}/dataset.parquet"}, "workable": common | {"snapshot_id": artifact_id, "key": f"voc/datasets/workable/{artifact_id}/dataset.parquet", "schema_version": "workable-v1", "content_sha256": frame_hash(frame)}, "lineage": {"run_id": run_id, "artifact_id": artifact_id, "mlflow_run_id": "fixture", "project_identity": "fixture", "rules": {}}}
    import hashlib
    payload = json_bytes(manifest)
    client = MemoryS3()
    client.objects = {manifest["raw"]["key"]: file.read_bytes(), manifest["workable"]["key"]: file.read_bytes(), f"voc/datasets/releases/{release}.json": payload, "voc/datasets/latest.json": json_bytes({"format_version": 1, "release_id": release, "manifest_key": f"voc/datasets/releases/{release}.json", "manifest_sha256": hashlib.sha256(payload).hexdigest()})}
    reader = SharedDatasetReader(settings, client=client)
    prepared = reader.read()
    raw = reader.read(release, stage="raw")
    assert prepared.data.equals(frame) and raw.data.equals(frame)
    assert sum(k.endswith(".parquet") for k in client.reads) == 2
    client.offline = True
    assert reader.read(release).data.equals(frame)
    assert not (settings.runtime_root / "raw").exists()
    assert not (settings.runtime_root / "publications.db").exists()
