"""Vector shape, dtype, finite-value and normalization invariants."""

import numpy as np


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
