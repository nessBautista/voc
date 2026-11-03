"""One manually triggered, locally bounded topic discovery workflow."""

from zenml import pipeline

from voc_ml.steps.topics import fit_topics, pin_topics, track_topics


@pipeline(enable_cache=False)
def topics_pipeline(run_id: str, experiment_name: str, runtime_root: str):
    pinned = pin_topics(run_id, runtime_root)
    fitted = fit_topics(pinned, runtime_root)
    track_topics.with_options(
        settings={"experiment_tracker": {"experiment_name": experiment_name}}
    )(fitted, runtime_root)
