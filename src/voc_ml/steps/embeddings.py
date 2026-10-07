"""Thin embedding steps passing checked references on a shared local filesystem."""

import json
from pathlib import Path
from typing import Annotated

from zenml import get_step_context, step

from voc_ml.embeddings.producer import load_run, update_run


@step(enable_cache=False)
def pin_embedding_input(producer_run_id: str) -> Annotated[dict, "input_reference"]:
    from voc_ml.embeddings import pin_input
    from voc_ml.embeddings.artifacts import write_input

    state = update_run(
        producer_run_id, zenml_run_id=str(get_step_context().pipeline_run.id)
    )
    request = state["requested"]
    pinned = pin_input(
        version=request["dataset_version"],
        source=request["source"],
        limit=request["limit"],
    )
    reference = write_input(pinned, Path(state["paths"]["run"]) / "input")
    update_run(producer_run_id, input_reference=reference)
    return reference


@step(enable_cache=False)
def prepare_embedding_features(
    input_reference: dict, producer_run_id: str
) -> Annotated[dict, "feature_reference"]:
    from voc.embeddings.profiles import resolve_profile
    from voc_ml.embeddings import FeatureConfig, prepare_features
    from voc_ml.embeddings.artifacts import read_input

    state = load_run(producer_run_id)
    profile = resolve_profile(state["requested"]["profile"])
    reference = prepare_features(
        read_input(input_reference),
        FeatureConfig(profile.preparation_policy),
        Path(state["paths"]["run"]) / "features",
    )
    update_run(producer_run_id, feature_reference=reference)
    return reference


@step(enable_cache=False)
def encode_embedding_features(
    feature_reference: dict, producer_run_id: str
) -> Annotated[dict, "encoding_reference"]:
    from voc_ml.embeddings import encode_features

    state = load_run(producer_run_id)
    request, paths = state["requested"], state["paths"]
    reference = encode_features(
        feature_reference,
        output_dir=Path(paths["run"]) / "encoding",
        cache_path=paths["cache"],
        model_dir=paths["models"],
        profile_name=request["profile"],
        batch_size=request["batch_size"],
        local_files_only=request["local_files_only"],
    )
    update_run(producer_run_id, encoding_reference=reference)
    return reference


@step(enable_cache=False)
def validate_embedding_outputs(
    encoding_reference: dict, input_reference: dict, producer_run_id: str
) -> Annotated[dict, "validated_reference"]:
    from voc_ml.embeddings.artifacts import read_encoding

    state = load_run(producer_run_id)
    read_encoding(
        encoding_reference,
        expected_input=input_reference,
        expected_profile=state["requested"]["profile"],
    )
    return encoding_reference


@step(enable_cache=False, experiment_tracker="voc-mlflow")
def track_embeddings(
    validated_reference: dict, producer_run_id: str
) -> Annotated[dict, "tracking_report"]:
    import mlflow

    from voc_ml.embeddings.artifacts import read_encoding

    active = mlflow.active_run()
    if active is None:
        raise RuntimeError("ZenML did not activate the MLflow experiment tracker")
    state = update_run(producer_run_id, mlflow_run_id=active.info.run_id)
    report = read_encoding(
        validated_reference,
        expected_input=state["input_reference"],
        expected_profile=state["requested"]["profile"],
    )
    mlflow.set_tags(
        {
            "producer_run_id": producer_run_id,
            "zenml_run_id": state["zenml_run_id"],
            "project_identity": state["project_identity"],
        }
    )
    profile = report["profile"]["config"]
    mlflow.log_params(
        {
            "model": profile["model"],
            "revision": profile["revision"],
            "profile_id": report["profile"]["profile_id"],
            "profile": report["profile"]["name"],
            "preparation_policy": report["feature"]["policy"],
            "feature_id": report["feature"]["feature_id"],
            "selection_kind": report["selection"]["kind"],
            "selection_id": report["selection"]["selection_id"],
            "dataset_release_id": report["dataset"]["release_id"],
            "encoder_id": report["encoder_id"],
            "batch_size": report["batch_size"],
        }
    )
    feature_report = json.loads(
        (
            Path(report["feature_reference"]["directory"]) / "feature-report.json"
        ).read_bytes()
    )
    metrics = {
        key: report[key]
        for key in (
            "rows",
            "unique_texts",
            "encoded_unique",
            "reused_unique",
            "encoding_calls",
            "truncated_unique",
            "truncated_rows",
            "max_tokens_observed",
            "duration_seconds",
            "peak_process_rss_bytes",
        )
        if report[key] is not None
    }
    metrics["source_rows"] = report["dataset"]["row_count"]
    metrics["changed_text_rows"] = feature_report["counts"]["changed_rows"]
    mlflow.log_metrics(metrics)
    mlflow.log_dict(report, "encoding-report.json")
    mlflow.log_dict(feature_report, "feature-report.json")
    return {
        "mlflow_run_id": active.info.run_id,
        "encoding_reference": validated_reference,
    }
