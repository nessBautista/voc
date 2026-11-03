"""Verify pinned inputs or pilot resources without starting models."""

import argparse
import json

from voc.paths import data_root
from voc.topics.scope import TopicSelection
from voc_ml.topics.preflight import preflight

DATASET = "3862e53a-e9f4-47af-a644-35c2cc7c1945"
EMBEDDING_RUN = "a3f2c83d-84c3-42cb-9cbe-8586df808491"
EMBEDDING_HASH = "b96fc23d9f32496312d1921d8e14e77cfcb226a8819e7328f0c4bff83447fbc2"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preflight", choices=["pilot", "full"])
    action.add_argument("--resources", action="store_true")
    action.add_argument("--create", action="store_true")
    action.add_argument("--run", choices=["G0", "G3", "R2"])
    action.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    if args.create or args.run or args.inspect:
        from voc_ml.topics.corpus import create_pilot, inspect_pilot, run_pilot
        from voc_ml.topics.store import local_store

        store = local_store()
        if args.create:
            result = create_pilot(
                DATASET,
                data_root() / "embeddings/runs" / EMBEDDING_RUN / "release",
                EMBEDDING_HASH,
                store=store,
            )
        elif args.run:
            result = run_pilot(args.run, store=store)
        else:
            result = inspect_pilot(store=store)
        report = {
            "study_id": result["study_id"],
            "protocol_sha256": result["protocol_sha256"],
            "rows": result["protocol"]["input"]["selected_rows"],
            "selection_sha256": result["protocol"]["input"]["selection_sha256"],
            "order": result["protocol"]["order"],
            "budget": result["protocol"]["budget"],
            "entries": {
                key: {k: v for k, v in entry.items() if k != "resource_preflight"}
                for key, entry in result["entries"].items()
            },
            "summaries": result.get("summaries", []),
            "comparisons": result.get("comparisons", []),
            "full_corpus_enabled": False,
            "model_requests": 0,
        }
        print(json.dumps(report, indent=2))
        return
    if args.resources:
        from voc_ml.topics.resources import resource_preflight

        print(json.dumps(resource_preflight(data_root()), indent=2))
        return
    # Reuse the same public preparation operation as the CLI.
    selection = (
        TopicSelection()
        if args.preflight == "pilot"
        else TopicSelection("full", None, None)
    )
    result = preflight(
        DATASET,
        data_root() / "embeddings/runs" / EMBEDDING_RUN / "release",
        EMBEDDING_HASH,
        selection,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
