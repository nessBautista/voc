import pandas as pd
import pyarrow.parquet as pq
from .parquet import prepare_frame, restore_frame
from .manifest import file_hash

def export_frame(frame, path):
    """Retain retry bytes and verify original values, including mixed raw scalars."""
    physical, encodings = prepare_frame(frame)
    logical_columns = [
        {
            "name": name,
            "pandas_dtype": str(frame[name].dtype),
            **({"encoding": encodings[name]} if name in encodings else {}),
        }
        for name in frame
    ]
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        physical.to_parquet(temporary, index=False)
        restored = restore_frame(pd.read_parquet(temporary), logical_columns)
        pd.testing.assert_frame_equal(frame, restored, check_exact=True)
        temporary.replace(path)
    restored = restore_frame(pd.read_parquet(path), logical_columns)
    pd.testing.assert_frame_equal(frame, restored, check_exact=True)
    schema = pq.read_schema(path)
    return {
        "row_count": len(frame),
        "size_bytes": path.stat().st_size,
        "sha256": file_hash(path),
        "columns": [
            column | {"arrow_type": str(field.type)}
            for column, field in zip(logical_columns, schema, strict=True)
        ],
    }
