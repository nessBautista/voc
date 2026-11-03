"""Run the bounded G0 acceptance check, or inspect a completed run without fitting."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from voc.paths import data_root
from voc.topics import read_topics

DATASET = "3862e53a-e9f4-47af-a644-35c2cc7c1945"
EMBEDDING_RUN = "a3f2c83d-84c3-42cb-9cbe-8586df808491"
EMBEDDING_HASH = "b96fc23d9f32496312d1921d8e14e77cfcb226a8819e7328f0c4bff83447fbc2"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="Fit once with local tracking")
    mode.add_argument("--run-id", help="Inspect only, without fitting")
    args = parser.parse_args()
    from voc_ml.topics.production import create_run, inspect_run

    if args.run:
        from voc_ml.topics import load_config
        from voc_ml.topics.store import local_store
        from voc_ml.topics.workflow import execute_workflow

        store = local_store()

        root = Path(__file__).resolve().parents[2]
        state = create_run(
            DATASET,
            data_root() / "embeddings/runs" / EMBEDDING_RUN / "release",
            EMBEDDING_HASH,
            load_config(root / "config/topics/base.toml"),
            store=store,
        )
        print("Topic producer: " + state["run_id"], flush=True)
        state = execute_workflow(state["run_id"], store=store)
    else:
        state = inspect_run(args.run_id)
    if state["state"] != "completed":
        raise ValueError("The producer did not complete")
    result = state["result"]
    bundle = read_topics(result["directory"], manifest_sha256=result["manifest_sha256"])
    if not bundle.info["counts"]["topic_count"]:
        raise ValueError(
            "Artifact is valid but no assigned topic is available to inspect"
        )
    # Read again in a separate process through the consumer CLI.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "voc.cli",
            "topics",
            "read",
            result["directory"],
            "--manifest-sha256",
            result["manifest_sha256"],
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    print(
        json.dumps(
            {
                "state": state["state"],
                "run_id": state["run_id"],
                "result": result,
                "counts": bundle.info["counts"],
                "worker_wall_seconds": state["worker_wall_seconds"],
                "peak_worker_rss_bytes": state["fit"]["peak_worker_rss_bytes"],
                "zenml_run_id": state["zenml_run_id"],
                "mlflow_run_id": state["mlflow_run_id"],
                "separate_process_read": "passed",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
