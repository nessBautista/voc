"""One BERTopic fit using supplied embeddings, without downloads or LLM calls."""

import math
from importlib.metadata import version

import numpy as np
import pandas as pd

from voc.topics.contracts import SCHEMA, validate_results
from voc.topics.scope import INPUT_SCHEMA, SCHEMA_V2

from .models import build_topic_model, representation_policy


def portable_parameters(value):
    """Record library defaults such as infinite UMAP distances as explicit strings."""
    if isinstance(value, dict):
        return {str(key): portable_parameters(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [portable_parameters(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def discover(inputs, config):
    # Validate text/vector alignment before importing optional model libraries.
    inputs.validate()
    config.__post_init__()
    if len(inputs.reviews) != config.sample_size:
        raise ValueError("Configuration and input size differ")
    model = build_topic_model(config)
    reducer = model.umap_model
    clusterer = model.hdbscan_model
    texts = inputs.reviews.model_text.tolist()
    try:
        labels, strengths = model.fit_transform(texts, embeddings=inputs.vectors)
    except ValueError as error:
        if "empty vocabulary" in str(error).lower():
            raise ValueError(
                "No usable topic vocabulary under the configured policy"
            ) from error
        raise
    assignments = pd.DataFrame(
        {
            "record_id": inputs.reviews.record_id,
            "topic_id": np.asarray(labels, dtype=np.int64),
            "membership_strength": strengths,
        }
    )
    topics = []
    groups = inputs.reviews.groupby(assignments.topic_id, sort=True)
    for tid, members in groups:
        first_ids = {}
        for rid, text in zip(members.record_id, members.model_text, strict=True):
            first_ids.setdefault(text, rid)
        evidence = []
        for text in model.get_representative_docs(tid) or []:
            if text not in first_ids:
                raise ValueError(
                    "BERTopic returned evidence outside its assigned group"
                )
            if first_ids[text] not in evidence:
                evidence.append(first_ids[text])
        topics.append(
            {
                "topic_id": int(tid),
                "size": len(members),
                "keywords": [
                    {"term": term, "weight": float(weight)}
                    for term, weight in (model.get_topic(tid) or [])
                    if term and weight > 0
                ],
                "evidence_ids": evidence,
            }
        )
    coordinates = np.asarray(reducer.embedding_, dtype=np.float32)
    counts = validate_results(
        inputs.reviews,
        assignments,
        topics,
        coordinates,
        schema=SCHEMA_V2
        if inputs.info.get("schema_version") == INPUT_SCHEMA
        else SCHEMA,
    )
    mapping = sorted(set(zip(clusterer.labels_.tolist(), labels, strict=True)))
    if (
        len(mapping) != len(set(clusterer.labels_))
        or len(mapping) != len(set(labels))
        or any((a == -1) != (b == -1) for a, b in mapping)
    ):
        raise ValueError("Unexpected HDBSCAN to BERTopic mapping")
    report = {
        "counts": counts,
        "cluster_to_topic": [
            {"cluster_id": int(a), "topic_id": int(b)} for a, b in mapping
        ],
        "representation": representation_policy(config),
        "effective_reduction": portable_parameters(reducer.get_params()),
        "effective_clustering": portable_parameters(clusterer.get_params()),
        "software": {
            name: version(name)
            for name in (
                "bertopic",
                "umap-learn",
                "hdbscan",
                "scikit-learn",
                "numpy",
                "scipy",
                "numba",
            )
        },
        "membership_strength": "HDBSCAN membership strength, not calibrated topic confidence or sentiment",
        "evidence_policy": "BERTopic c-TF-IDF representatives, first ID per distinct member text",
        "coordinates": "UMAP grouping coordinates, not a separate 2D visualization",
    }
    return assignments, topics, coordinates, report
