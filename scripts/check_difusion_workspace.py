"""Verify copied inputs and isolated tracking; optionally load cached base models."""

import argparse
import gc
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from voc.datasets.manifest import file_hash, frame_hash
from voc.embeddings.validation import load_release

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load-models", action="store_true")
    args = parser.parse_args()
    require(Path(sys.prefix) == ROOT / ".venv", "Use scripts/difusion.sh")
    require(os.environ.get("VOC_DATA_DIR") == str(DATA), "Unexpected runtime")
    require(DATA.resolve() == DATA, "Runtime points outside the checkout")
    require(os.environ.get("HF_HUB_OFFLINE") == "1", "Offline model mode required")

    print("Checking copied files and internal model links...", flush=True)
    receipt = json.loads((DATA / "setup/copy-receipt.json").read_text())
    for entry in receipt["files"]:
        path = DATA / entry["destination"]
        require(path.resolve(strict=True).is_relative_to(DATA), "Copy escapes runtime")
        if "link" in entry:
            require(
                path.is_symlink() and os.readlink(path) == entry["link"], "Link changed"
            )
        else:
            require(file_hash(path) == entry["sha256"], f"Copy changed: {path}")
    manifest_path = DATA / "inputs/corpus/manifest.json"
    require(
        file_hash(manifest_path) == receipt["extracted_manifest_sha256"],
        "Manifest changed",
    )
    manifest = json.loads(manifest_path.read_text())
    frame = pd.read_parquet(DATA / "inputs/corpus/dataset.parquet")
    require(len(frame) == manifest["workable"]["row_count"], "Corpus count differs")
    require(
        frame_hash(frame) == manifest["workable"]["content_sha256"],
        "Corpus content differs",
    )
    expected = frame[["record_id", "model_text"]].reset_index(drop=True)
    expected["source_row"] = range(len(expected))
    print("Checking full corpus/vector alignment and normalization...", flush=True)
    bundle = load_release(DATA / "inputs/embeddings", expected_reviews=expected)

    print("Checking local ZenML and MLflow stores...", flush=True)
    from mlflow.tracking import MlflowClient
    from zenml.client import Client

    client = Client()
    require(
        client.zen_store.config.url.startswith(f"sqlite:///{DATA}/"),
        "Nonlocal ZenML store",
    )
    stack = client.active_stack_model
    require(stack.name == "voc-local", "Unexpected active stack")
    components = {
        str(k): values[0].configuration for k, values in stack.components.items()
    }
    require(
        components["artifact_store"]["path"] == str(DATA / "zenml-artifacts"),
        "Artifact store differs",
    )
    tracking_uri = f"sqlite:///{DATA}/mlflow/mlflow.db"
    require(
        os.environ["MLFLOW_TRACKING_URI"] == tracking_uri, "MLflow environment differs"
    )
    require(
        components["experiment_tracker"]["tracking_uri"] == tracking_uri,
        "Tracker differs",
    )
    experiment = MlflowClient().get_experiment_by_name("voc-datasets")
    require(experiment is not None, "Run voc-ml setup first")
    require(
        experiment.artifact_location.startswith((DATA / "mlflow").as_uri() + "/"),
        "Experiment artifact path differs",
    )

    models = {}
    if args.load_models:
        from voc.embeddings.profiles import resolve_profile
        from voc_ml.embeddings.sentence_encoder import SentenceEncoder
        from voc_ml.sentiment.robertuito import RobertuitoClassifier, SentimentConfig

        print("Loading cached MiniLM on CPU, offline...", flush=True)
        encoder = SentenceEncoder(
            resolve_profile(),
            DATA / "models/sentence-transformers",
            local_files_only=True,
        )
        models["embeddings"] = {
            "revision": encoder.resolved_revision,
            "dimensions": encoder.profile.dimensions,
        }
        del encoder
        gc.collect()
        print("Loading cached base Robertuito on CPU, offline...", flush=True)
        classifier = RobertuitoClassifier(
            SentimentConfig(), DATA / "models/sentiment", local_files_only=True
        )
        models["sentiment"] = {
            "revision": classifier.config.revision,
            "labels": classifier.mapping,
        }
        del classifier
        gc.collect()

    result = {
        "state": "verified",
        "observed_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "runtime": str(DATA),
        "copied_entries_verified": len(receipt["files"]),
        "dataset_release_id": manifest["release_id"],
        "rows": len(frame),
        "vectors": {
            "shape": list(bundle.vectors.shape),
            "dtype": str(bundle.vectors.dtype),
        },
        "stack": stack.name,
        "tracking_uri": tracking_uri,
        "loaded_models": models,
        "fits": 0,
        "predictions": 0,
        "provider_requests": 0,
    }
    output = DATA / "setup/workspace-check.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
