"""Shared release format: no orchestration, database, or AWS client setup."""
import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from .parquet import ENCODING
MAX_FILE_BYTES = 1024**3
MAX_JSON_BYTES = 1024**2

def json_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()



def frame_hash(frame):
    return hashlib.sha256(
        frame.to_json(orient="records", force_ascii=False).encode()
    ).hexdigest()



def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()



def canonical_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Expected a canonical UUID")
    return value



def validate_manifest(manifest, objects):
    """Check this format's required identities, hashes and exact in-root keys."""
    if manifest["format_version"] not in (1, 2):
        raise ValueError("Unsupported release manifest format")
    canonical_id(manifest["release_id"])
    canonical_id(manifest["lineage"]["run_id"])
    for stage, identity in (("raw", "revision_id"), ("workable", "snapshot_id")):
        item = manifest[stage]
        native = canonical_id(item[identity])
        if item["key"] != objects.key(f"{stage}/{native}/dataset.parquet"):
            raise ValueError("Manifest object key is outside its expected location")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("Invalid dataset checksum")
        if (
            type(item["row_count"]) is not int
            or item["row_count"] < 0
            or type(item["size_bytes"]) is not int
            or not 0 < item["size_bytes"] <= MAX_FILE_BYTES
        ):
            raise ValueError("Invalid dataset size")
        if not item["columns"] or len({c["name"] for c in item["columns"]}) != len(
            item["columns"]
        ):
            raise ValueError("Invalid column schema")
        for column in item["columns"]:
            if "encoding" in column and (
                manifest["format_version"] != 2
                or column["encoding"] != ENCODING
                or column["pandas_dtype"] != "object"
                or column["arrow_type"] != "string"
            ):
                raise ValueError("Unsupported manifest column encoding")
            if not all(
                isinstance(column[k], str)
                for k in ("name", "arrow_type", "pandas_dtype")
            ):
                raise ValueError("Invalid column type")
    if manifest["workable"]["snapshot_id"] != manifest["lineage"]["artifact_id"]:
        raise ValueError("Manifest artifact identity differs")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest["workable"]["content_sha256"]):
        raise ValueError("Invalid content checksum")
    if not isinstance(manifest["lineage"]["rules"], dict):
        raise TypeError("Invalid preparation rules")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", manifest["publisher_id"]):
        raise ValueError("Invalid publisher ID")
    datetime.fromisoformat(manifest["created_at"])
