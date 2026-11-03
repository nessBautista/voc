"""Bounded v2 artifact decoding, before dataframe or coordinate materialization."""

import numpy as np
import pyarrow.parquet as pq

from .contracts import ASSIGNMENT_COLUMNS, SELECTION_COLUMNS
from .scope import MAX_ROWS

FILE_LIMITS = {
    "selection.parquet": 128 * 2**20,
    "assignments.parquet": 16 * 2**20,
    "topics.json": 32 * 2**20,
    "coordinates.npy": 16 * 2**20,
    "execution.json": 4 * 2**20,
}


def read_tables(directory):
    frames = []
    for name, columns in (
        ("selection.parquet", SELECTION_COLUMNS),
        ("assignments.parquet", ASSIGNMENT_COLUMNS),
    ):
        parquet = pq.ParquetFile(directory / name)
        try:
            metadata = parquet.metadata
            if (
                not 1 <= metadata.num_rows <= MAX_ROWS
                or tuple(parquet.schema_arrow.names) != columns
            ):
                raise ValueError("Invalid topic parquet shape or columns")
            size = sum(
                metadata.row_group(i).total_byte_size
                for i in range(metadata.num_row_groups)
            )
            if size > 256 * 2**20:
                raise ValueError("Topic parquet decompressed size exceeds limit")
            frames.append(parquet.read().to_pandas())
        finally:
            parquet.close()
    coordinates = np.load(
        directory / "coordinates.npy", allow_pickle=False, mmap_mode="r"
    )
    if (
        coordinates.ndim != 2
        or not 1 <= coordinates.shape[0] <= MAX_ROWS
        or not 2 <= coordinates.shape[1] <= 20
        or coordinates.dtype.kind != "f"
        or coordinates.dtype.itemsize not in (4, 8)
    ):
        raise ValueError("Invalid bounded coordinate shape")
    return *frames, coordinates
