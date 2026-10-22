"""Reduction cache identity, artifact integrity and batch-only adapter checks."""

import json
from dataclasses import replace
from unittest.mock import Mock

import numpy as np
import pytest

from tests.unit.test_analysis_inputs import fixture_inputs
from voc.datasets.manifest import file_hash
from voc_ml.analysis import SelectionConfig, select_inputs
from voc_ml.analysis.config import ReductionConfig
from voc_ml.topics import reduction as r


@pytest.fixture
def inputs():
    return select_inputs(*fixture_inputs())


@pytest.fixture
def fitter(monkeypatch):
    fit = Mock(
        side_effect=lambda vectors, config: np.random.default_rng(
            config.random_state
        ).normal(size=(len(vectors), config.n_components))
    )
    monkeypatch.setattr(r, "_fit", fit)
    return fit


def test_roundtrip_cache_skips_fitting_and_contains_no_model(inputs, fitter, tmp_path):
    config = ReductionConfig()
    fresh = r.reduce_inputs(inputs, config, cache_dir=tmp_path)
    cached = r.reduce_inputs(inputs, config, cache_dir=tmp_path)
    assert not fresh.reused and cached.reused and fitter.call_count == 1
    np.testing.assert_array_equal(fresh.coordinates, cached.coordinates)
    assert not cached.coordinates.flags.writeable
    assert sorted(p.name for p in fresh.directory.iterdir()) == [
        "coordinates.npy",
        "manifest.json",
    ]
    assert fresh.info == cached.info


def test_seed_settings_selection_and_software_change_identity(
    inputs, fitter, tmp_path, monkeypatch
):
    config = ReductionConfig()
    outputs = [r.reduce_inputs(inputs, config, cache_dir=tmp_path)]
    for changed in (replace(config, random_state=43), replace(config, n_neighbors=10)):
        outputs.append(r.reduce_inputs(inputs, changed, cache_dir=tmp_path))
    smaller = select_inputs(*fixture_inputs(), selection=SelectionConfig("first-n", 24))
    outputs.append(r.reduce_inputs(smaller, config, cache_dir=tmp_path))
    original = r.version
    monkeypatch.setattr(
        r,
        "version",
        lambda name: "different" if name == "umap-learn" else original(name),
    )
    outputs.append(r.reduce_inputs(inputs, config, cache_dir=tmp_path))
    assert len({o.info["reduction_id"] for o in outputs}) == 5
    assert fitter.call_count == 5


@pytest.mark.parametrize("mutation", ["order", "vectors", "metadata"])
def test_changed_input_rejected_before_fitting(inputs, fitter, tmp_path, mutation):
    if mutation == "order":
        inputs = replace(inputs, reviews=inputs.reviews.iloc[::-1])
    elif mutation == "vectors":
        inputs = replace(inputs, vectors=inputs.vectors[::-1].copy())
    else:
        inputs.reviews.loc[0, "model_text"] = "changed"
    with pytest.raises(ValueError):
        r.reduce_inputs(inputs, ReductionConfig(), cache_dir=tmp_path)
    fitter.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "mutation", ["bytes", "missing", "descriptor", "shape", "nan", "dtype"]
)
def test_corrupt_cache_rejected_without_refitting(inputs, fitter, tmp_path, mutation):
    config = ReductionConfig()
    result = r.reduce_inputs(inputs, config, cache_dir=tmp_path)
    path, marker = (
        result.directory / "coordinates.npy",
        result.directory / "manifest.json",
    )
    info = json.loads(marker.read_text())
    if mutation == "bytes":
        path.write_bytes(path.read_bytes() + b"bad")
    elif mutation == "missing":
        marker.unlink()
    elif mutation == "descriptor":
        info["descriptor"]["parameters"]["random_state"] = 7
        marker.write_text(json.dumps(info))
    else:
        values = result.coordinates.copy()
        if mutation == "shape":
            values = values[:-1]
        elif mutation == "nan":
            values[0, 0] = float("nan")
        else:
            values = values.astype("float64")
        np.save(path, values, allow_pickle=False)
        info["artifact"].update(sha256=file_hash(path), size_bytes=path.stat().st_size)
        marker.write_text(json.dumps(info))
    with pytest.raises(ValueError):
        r.reduce_inputs(inputs, config, cache_dir=tmp_path)
    assert fitter.call_count == 1


def test_failed_fit_does_not_leave_completed_entry(inputs, fitter, tmp_path):
    fitter.side_effect = RuntimeError("interrupted")
    with pytest.raises(RuntimeError, match="interrupted"):
        r.reduce_inputs(inputs, ReductionConfig(), cache_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_adapter_refuses_new_reordered_or_mutated_batches(inputs, fitter, tmp_path):
    config = ReductionConfig()
    result = r.reduce_inputs(inputs, config, cache_dir=tmp_path)
    adapter = r.CachedReduction(inputs, result, config)
    assert adapter.fit(inputs.vectors) is adapter
    np.testing.assert_array_equal(adapter.transform(inputs.vectors), result.coordinates)
    for values in (
        inputs.vectors[::-1].copy(),
        inputs.vectors[:-1],
        inputs.vectors.astype("float64"),
    ):
        with pytest.raises(ValueError, match="original ordered batch"):
            adapter.transform(values)
    with pytest.raises(ValueError, match="unsupervised"):
        adapter.fit(inputs.vectors, y=np.zeros(len(inputs.reviews)))
    with pytest.raises(ValueError):
        r.CachedReduction(inputs, result, replace(config, random_state=0))
    mutated = result.coordinates.copy()
    mutated[0, 0] += 1
    with pytest.raises(ValueError, match="checksum"):
        r.CachedReduction(inputs, replace(result, coordinates=mutated), config)


def test_nonfinite_fit_not_published(inputs, fitter, tmp_path):
    fitter.side_effect = lambda v, c: np.full((len(v), c.n_components), np.inf)
    with pytest.raises(ValueError, match="finite"):
        r.reduce_inputs(inputs, ReductionConfig(), cache_dir=tmp_path)
    assert not list(tmp_path.iterdir())
