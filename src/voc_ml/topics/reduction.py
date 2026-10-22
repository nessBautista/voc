"""Reusable UMAP coordinates bound to pinned inputs, settings and software.

Only arrays and JSON are cached, never pickled reducers. Cache hits validate the
manifest, checksum, dtype, shape and finite values. Invalid entries fail clearly
rather than silently changing the evidence of a previous experiment.
"""

import json
import os
import platform
import shutil
import tempfile
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path

import numpy as np

from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import canonical_json, fingerprint
from voc.paths import data_root
from voc_ml.analysis.config import ReductionConfig
from voc_ml.analysis.inputs import AnalysisInput, _npy_identity


@dataclass(frozen=True)
class ReductionResult:
    coordinates: np.ndarray
    info: dict
    directory: Path
    reused: bool

    def validate(self, inputs, config):
        inputs.validate()
        descriptor = _descriptor(inputs, config)
        if self.info.get("descriptor") != descriptor or self.info.get(
            "reduction_id"
        ) != fingerprint(descriptor):
            raise ValueError(
                "Reduction identity differs from the selected input/settings"
            )
        if self.info.get("schema_version") != "voc-topic-reduction-v1":
            raise ValueError("Unsupported reduction schema")
        _coordinates(self.coordinates, len(inputs.reviews), config.n_components)
        artifact = self.info.get("artifact", {})
        digest, size = _npy_identity(self.coordinates)
        if artifact != {
            "path": "coordinates.npy",
            "sha256": digest,
            "size_bytes": size,
            "shape": list(self.coordinates.shape),
            "dtype": "<f4",
        }:
            raise ValueError("Reduction coordinates differ from their checksum/shape")


def _descriptor(inputs, config):
    if not isinstance(inputs, AnalysisInput) or not isinstance(config, ReductionConfig):
        raise TypeError("Expected AnalysisInput and ReductionConfig")
    config.__post_init__()
    if len(inputs.reviews) <= max(config.n_neighbors, config.n_components + 1):
        raise ValueError("Selection is too small for configured reduction")
    return {
        "adapter": "umap-cpu-single-thread-v1",
        "input_id": inputs.info["input_id"],
        "embedding": inputs.info["embedding"],
        "ordered_ids_sha256": inputs.info["selection"]["ordered_ids_sha256"],
        "vectors_sha256": inputs.info["vectors_sha256"],
        "vector_shape": inputs.info["vector_shape"],
        "parameters": {**asdict(config), "n_jobs": 1},
        "software": {
            name: version(name)
            for name in (
                "umap-learn",
                "numpy",
                "numba",
                "llvmlite",
                "pynndescent",
                "scipy",
                "scikit-learn",
            )
        }
        | {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
    }


def _coordinates(values, rows, dimensions):
    if (
        not isinstance(values, np.ndarray)
        or values.shape != (rows, dimensions)
        or values.dtype.str != "<f4"
        or not values.flags.c_contiguous
        or not np.isfinite(values).all()
    ):
        raise ValueError(
            "Reduction coordinates must be finite C-order float32 with the expected shape"
        )


def _fit(vectors, config):
    from umap import UMAP

    # Do not mutate the reusable published vectors. A seed and one worker make
    # repeats comparable within the recorded software/hardware environment.
    return UMAP(**asdict(config), n_jobs=1).fit_transform(vectors.copy())


def _load(directory, inputs, config, reused):
    if directory.is_symlink() or any(
        (directory / name).is_symlink() for name in ("manifest.json", "coordinates.npy")
    ):
        raise ValueError("Reduction cache entries must not be symlinks")
    try:
        info = json.loads((directory / "manifest.json").read_bytes())
        path = directory / "coordinates.npy"
        artifact = info["artifact"]
        if (
            artifact["path"] != "coordinates.npy"
            or path.stat().st_size != artifact["size_bytes"]
            or file_hash(path) != artifact["sha256"]
        ):
            raise ValueError("Reduction cache checksum/size mismatch")
        coordinates = np.load(path, allow_pickle=False)
    except (OSError, KeyError, TypeError) as error:
        raise ValueError("Incomplete or invalid reduction cache entry") from error
    result = ReductionResult(coordinates, info, directory, reused)
    result.validate(inputs, config)
    coordinates.flags.writeable = False
    return result


def reduce_inputs(inputs, config, *, cache_dir=None):
    """Return verified coordinates, reusing an identical cached reduction if present.

    ``cache_dir`` is the directory containing keyed entries. The default is
    VOC_DATA_DIR/cache/topics/reduction. A concurrent writer for the same key
    raises FileExistsError, so a caller can retry after that writer completes.
    """
    descriptor = _descriptor(inputs, config)
    inputs.validate()
    if not np.isfinite(inputs.vectors).all():
        raise ValueError("Input vectors must be finite")
    key = fingerprint(descriptor)
    root = (
        Path(cache_dir)
        if cache_dir is not None
        else data_root() / "cache/topics/reduction"
    )
    directory = root / key
    if os.path.lexists(directory):
        return _load(directory, inputs, config, True)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / f".{key}.lock"
    with lock.open("x"):
        staging = None
        try:
            if directory.exists():
                return _load(directory, inputs, config, True)
            values = np.array(
                _fit(inputs.vectors, config), dtype="<f4", order="C", copy=True
            )
            _coordinates(values, len(inputs.reviews), config.n_components)
            staging = Path(tempfile.mkdtemp(prefix=f".staging-{key}-", dir=root))
            path = staging / "coordinates.npy"
            np.save(path, values, allow_pickle=False)
            info = {
                "schema_version": "voc-topic-reduction-v1",
                "reduction_id": key,
                "descriptor": descriptor,
                "artifact": {
                    "path": "coordinates.npy",
                    "sha256": file_hash(path),
                    "size_bytes": path.stat().st_size,
                    "shape": list(values.shape),
                    "dtype": "<f4",
                },
            }
            (staging / "manifest.json").write_bytes(canonical_json(info))
            _load(staging, inputs, config, False)
            if os.path.lexists(directory):
                raise FileExistsError("Reduction cache destination already exists")
            staging.rename(directory)
            return _load(directory, inputs, config, False)
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
            lock.unlink()


class CachedReduction:
    """BERTopic reducer adapter restricted to its exact original discovery batch.

    It cannot assign future reviews. Validation happens before BERTopic can
    replace nonfinite coordinates internally. No fitted UMAP model is persisted.
    """

    def __init__(self, inputs, reduction, config):
        reduction.validate(inputs, config)
        self.vector_identity = _npy_identity(inputs.vectors)
        self.coordinates = reduction.coordinates.copy()
        self.coordinate_identity = _npy_identity(self.coordinates)
        self.coordinates.flags.writeable = False

    def fit(self, X, y=None):
        if y is not None:
            raise ValueError("Cached reduction supports unsupervised discovery only")
        self.transform(X)
        return self

    def transform(self, X):
        if not isinstance(X, np.ndarray) or _npy_identity(X) != self.vector_identity:
            raise ValueError(
                "Cached reduction only supports its original ordered batch"
            )
        if _npy_identity(self.coordinates) != self.coordinate_identity:
            raise ValueError("Cached reduction coordinates were modified")
        return self.coordinates.copy()
