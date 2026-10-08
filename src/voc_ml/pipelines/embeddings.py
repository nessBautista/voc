"""Manually triggered local embedding production with explicit vector-cache reuse."""

from zenml import pipeline

from voc_ml.steps.embeddings import (
    encode_embedding_features,
    pin_embedding_input,
    prepare_embedding_features,
    track_embeddings,
    validate_embedding_outputs,
)


@pipeline(enable_cache=False)
def embeddings_pipeline(producer_run_id: str, experiment_name: str):
    pinned = pin_embedding_input(producer_run_id)
    features = prepare_embedding_features(pinned, producer_run_id)
    encoded = encode_embedding_features(features, producer_run_id)
    validated = validate_embedding_outputs(encoded, pinned, producer_run_id)
    track_embeddings.with_options(
        settings={"experiment_tracker": {"experiment_name": experiment_name}}
    )(validated, producer_run_id)
