"""One explicitly requested interpretation. ZenML caching is disabled."""

from zenml import pipeline

from voc_ml.steps.interpretation import interpret_topic


@pipeline(enable_cache=False)
def interpretation_pipeline(key: str, ledger_root: str):
    interpret_topic.with_options(
        settings={"experiment_tracker": {"experiment_name": "voc-interpretations"}}
    )(key, ledger_root)
