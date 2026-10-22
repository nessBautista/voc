"""Local workflow operations reused by thin ZenML steps and offline tests."""

import sys
from dataclasses import asdict
from datetime import datetime
from importlib.metadata import version

import pandas as pd

from voc.datasets.manifest import file_hash, frame_hash
from voc.embeddings.contracts import DatasetIdentity, fingerprint
from voc_ml.sentiment import SentimentConfig, classify_reviews

from . import artifacts
from .config import AnalysisConfig
from .inputs import pin_inputs
from .producer import load_run, now, run_directory, update_run


def pin(run_id):
    state = load_run(run_id)
    request = state["requested"]
    if state["kind"] == "analysis":
        config = AnalysisConfig.from_dict(state["configuration"]["analysis"])
        inputs = pin_inputs(
            dataset_version=request["dataset_version"],
            embedding_version=request["embedding_version"],
            profile=request["profile"],
            source=request["source"],
            selection=config.selection,
        )
        config.validate_row_count(len(inputs.reviews))
        reference = artifacts.write_input(inputs, run_directory(run_id) / "input")
        identity = inputs.info
    else:
        from voc import collector

        snapshot = collector.get_dataset(
            stage="prepared",
            source=request["source"],
            version=request["dataset_version"],
        )
        dataset = DatasetIdentity(
            **{k: snapshot.info[k] for k in DatasetIdentity.__dataclass_fields__}
        )
        frame = snapshot.data
        if (
            len(frame) != dataset.row_count
            or frame_hash(frame) != dataset.content_sha256
        ):
            raise ValueError("Dataset content differs from pinned identity")
        if (
            not frame.columns.is_unique
            or not {"record_id", "model_text"} <= set(frame.columns)
            or frame.record_id.duplicated().any()
            or not frame.record_id.map(
                lambda x: isinstance(x, str) and bool(x.strip())
            ).all()
        ):
            raise ValueError("Invalid sentiment dataset review identity")
        limit = request["limit"]
        if limit is not None and limit > len(frame):
            raise ValueError("Requested sample exceeds dataset rows")
        reviews = frame.iloc[:limit].copy().reset_index(drop=True)
        if reviews.empty:
            raise ValueError("Cannot classify an empty dataset")
        path = run_directory(run_id) / "reviews.parquet"
        reviews.to_parquet(path, index=False)
        identity = {
            "dataset": dataset.as_dict(),
            "selection": {
                "kind": "full" if limit is None else "first-n",
                "size": len(reviews),
                "ordered_ids_sha256": fingerprint(reviews.record_id.tolist()),
            },
            "reviews_sha256": frame_hash(reviews),
        }
        identity["input_id"] = fingerprint(identity)
        reference = artifacts.write_json(
            run_directory(run_id) / "input.json",
            {
                "info": identity,
                "reviews": {"path": str(path), "sha256": file_hash(path)},
            },
        )
        read_sentiment_input(reference)
    # Keep inspect/CLI output compact, even for a full corpus. The checked input
    # artifact retains the complete ordered review IDs and vector positions.
    summary = {
        k: v for k, v in identity.items() if k in ("input_id", "dataset", "embedding")
    }
    summary["selection"] = {
        k: v
        for k, v in identity["selection"].items()
        if k not in ("record_ids", "positions", "strata")
    }
    update_run(run_id, input_reference=reference, inputs=summary)
    return reference


def read_sentiment_input(reference):
    record = artifacts.read_json(reference)
    reviews = pd.read_parquet(artifacts.checked_path(record["reviews"]))
    identity = record["info"]
    if (
        frame_hash(reviews) != identity["reviews_sha256"]
        or len(reviews) != identity["selection"]["size"]
        or fingerprint(reviews.record_id.tolist())
        != identity["selection"]["ordered_ids_sha256"]
        or fingerprint({k: v for k, v in identity.items() if k != "input_id"})
        != identity["input_id"]
    ):
        raise ValueError("Sentiment input identity mismatch")
    return reviews, identity


def topics(input_reference, run_id):
    from voc_ml.topics import discover

    state = load_run(run_id)
    inputs = artifacts.read_input(input_reference)
    result = discover(
        inputs, AnalysisConfig.from_dict(state["configuration"]["analysis"])
    )
    reference = artifacts.write_json(
        run_directory(run_id) / "topics.json", result.as_dict()
    )
    update_run(run_id, topic_reference=reference)
    return reference


def sentiment(input_reference, run_id):
    state = load_run(run_id)
    reviews = (
        artifacts.read_input(input_reference).reviews
        if state["kind"] == "analysis"
        else read_sentiment_input(input_reference)[0]
    )
    try:
        result = classify_reviews(
            reviews,
            SentimentConfig(**state["configuration"]["sentiment"]),
            local_files_only=state["requested"]["local_files_only"],
        )
    except Exception as error:
        if hasattr(error, "report"):
            update_run(run_id, sentiment_progress=error.report["progress_path"])
        raise
    reference = artifacts.write_json(
        run_directory(run_id) / "sentiment.json",
        {
            "sentiments": [asdict(row) for row in result.sentiments],
            "diagnostics": result.diagnostics,
            "report": result.report,
        },
    )
    update_run(
        run_id,
        sentiment_reference=reference,
        sentiment_progress=result.report["progress_path"],
    )
    return reference


def evidence(input_reference, sentiment_reference, run_id, topic_reference=None):
    """Validate joins and collect portable evidence, with explicit metric scope."""
    from voc.analysis.contracts import validate_results

    state = load_run(run_id)
    if (
        input_reference != state["input_reference"]
        or sentiment_reference != state["sentiment_reference"]
    ):
        raise ValueError("Step references differ from producer lineage")
    if state["kind"] == "analysis":
        inputs = artifacts.read_input(input_reference)
        reviews, identity = inputs.reviews, inputs.info
        if topic_reference != state["topic_reference"]:
            raise ValueError("Topic reference differs from producer lineage")
        discovered = artifacts.read_topics(topic_reference, inputs)
        if discovered.report["configuration"] != state["configuration"]["analysis"]:
            raise ValueError("Topic configuration differs from request")
    else:
        reviews, identity = read_sentiment_input(input_reference)
        discovered = None
    sentiments, value = artifacts.read_sentiments(
        sentiment_reference, reviews.record_id
    )
    report = value["report"]
    if report["configuration"] != state["configuration"]["sentiment"]:
        raise ValueError("Sentiment configuration differs from request")
    coverage = (
        validate_results(
            reviews.record_id.tolist(),
            discovered.assignments,
            discovered.topics,
            sentiments,
        )
        if discovered is not None
        else report["counts"]
    )
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = int(peak if sys.platform == "darwin" else peak * 1024)
    except (ImportError, AttributeError):
        peak = None
    result = {
        "schema_version": "voc-workflow-evidence-v1",
        "run_id": run_id,
        "kind": state["kind"],
        "zenml_run_id": state["zenml_run_id"],
        "code_sha256": state["project_identity"],
        "inputs": identity,
        "configuration": state["configuration"],
        "coverage": coverage,
        "references": {
            "input": input_reference,
            "sentiment": sentiment_reference,
            "topics": topic_reference,
        },
        "sentiment": report,
        "topics": None if discovered is None else discovered.report,
        "software": {
            name: version(name)
            for name in ("zenml", "mlflow", "pandas", "numpy", "duckdb")
        },
        "measurement": {
            "duration_seconds": (
                datetime.fromisoformat(now())
                - datetime.fromisoformat(state["started_at"])
            ).total_seconds(),
            "duration_scope": "producer start through validation, before tracking and finalization",
            "peak_process_rss_bytes": peak,
            "memory_scope": "validation process lifetime high-water RSS, including earlier local steps when shared",
        },
    }
    if discovered is not None:
        result["software"].update(discovered.report["software"])
    result["software"].update(report["descriptor"]["software"])
    reference = artifacts.write_json(run_directory(run_id) / "evidence.json", result)
    update_run(run_id, evidence_reference=reference)
    return reference
