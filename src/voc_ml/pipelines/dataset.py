"""Assemble raw revision lookup, preparation, and dataset tracking."""

from zenml import pipeline

from voc.storage import load_storage
from voc_ml.steps.raw import raw_revision
from voc_ml.steps.preparation import prepare
from voc_ml.steps.tracking import track
from voc_ml.tracking import experiment_name


@pipeline(enable_cache=False)
def dataset_pipeline(
    config: dict, rules: dict, refresh: bool, operation_id: str, project_identity: str
):
    reference = raw_revision(config, refresh, operation_id)
    _workable, report = prepare(reference, rules, project_identity)
    # Record the selected experiment in this run's step configuration.
    track.with_options(
        settings={
            "experiment_tracker": {"experiment_name": experiment_name(load_storage())}
        }
    )(report, project_identity)

