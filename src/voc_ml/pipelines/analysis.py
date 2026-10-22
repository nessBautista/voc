"""Manually triggered workflows over already prepared datasets and embeddings."""

from zenml import pipeline

from voc_ml.steps.analysis import (
    classify_analysis_sentiment,
    discover_analysis_topics,
    pin_analysis_input,
    track_analysis,
    validate_analysis_outputs,
    validate_sentiment_outputs,
)


@pipeline(enable_cache=False)
def analysis_pipeline(run_id: str, experiment_name: str):
    inputs = pin_analysis_input(run_id)
    topics = discover_analysis_topics(inputs, run_id)
    sentiments = classify_analysis_sentiment(inputs, run_id)
    evidence = validate_analysis_outputs(inputs, topics, sentiments, run_id)
    track_analysis.with_options(
        settings={"experiment_tracker": {"experiment_name": experiment_name}}
    )(evidence, run_id)


@pipeline(enable_cache=False)
def sentiment_pipeline(run_id: str, experiment_name: str):
    inputs = pin_analysis_input(run_id)
    sentiments = classify_analysis_sentiment(inputs, run_id)
    evidence = validate_sentiment_outputs(inputs, sentiments, run_id)
    track_analysis.with_options(
        settings={"experiment_tracker": {"experiment_name": experiment_name}}
    )(evidence, run_id)
