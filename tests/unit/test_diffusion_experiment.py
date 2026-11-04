"""Frozen sampling must preserve vector alignment and exclude known label texts."""

import numpy as np
import pandas as pd
import pytest

from tests.unit.test_analysis_inputs import fixture_inputs
from voc_ml.analysis import select_inputs
from voc_ml.experiments.operations import choose_sample


def test_sampling_precedes_filtering_and_excludes_annotation_duplicates():
    snapshot, bundle = fixture_inputs()
    full = select_inputs(snapshot, bundle)
    annotations = pd.DataFrame({"record_id": ["review-002"]})
    kwargs = {"start": "2026-01-01", "end": "2026-03-01", "size": 20, "seed": 42}
    sampled, audit = choose_sample(full, annotations, **kwargs)
    repeated, _ = choose_sample(full, annotations, **kwargs)
    assert sampled.info == repeated.info
    assert len(sampled.reviews) == 20
    assert not set(sampled.reviews.record_id) & {"review-002", "review-003"}
    assert not sampled.reviews.model_text.eq("Reseña 1").any()
    assert sum(row["selected"] for row in audit["coverage"]) == 20
    assert sum(row["rows"] for row in audit["weekly_selected"]) == 20
    assert audit["annotation_overlap_excluded"] == 2
    np.testing.assert_array_equal(
        sampled.vectors, full.vectors[sampled.reviews.vector_row]
    )
    assert not audit["independent_sentiment_test"]
    with pytest.raises(ValueError):
        choose_sample(full, annotations, **(kwargs | {"size": 100}))
    with pytest.raises(ValueError, match="absent"):
        choose_sample(full, pd.DataFrame({"record_id": ["absent"]}), **kwargs)
