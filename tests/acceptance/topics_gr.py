"""Bounded operator acceptance: create once, run one slot, or inspect without fitting."""

import argparse
import json
from importlib.metadata import version

from voc.embeddings.shared.contracts import canonical_json
from voc.topics import read_topics
from voc_ml.topics.store import local_store
from voc_ml.topics.studies import create_study, inspect_study, load_study, run_variant

G0 = "7ce4db4a-2890-4489-ac14-d1bf0a5ad35d"
G0_HASH = "902f2b1e9ea240136deecec7e375c38457ee036f2a5120e769b28596d26aeea4"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--run", choices=["G0", "G1", "G2", "G3", "R1", "R2"])
    mode.add_argument("--inspect", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    store = local_store()
    pointer = store.root / "topics/acceptance-gr.json"
    if args.create:
        parent = read_topics(store.directory(G0) / "release", manifest_sha256=G0_HASH)
        # A setup error should be corrected before reserving any model attempt.
        for name, expected in parent.execution["fit"]["software"].items():
            if version(name) != expected:
                raise ValueError(f"Environment differs from G0: {name}")
        if pointer.exists():
            sid = json.loads(pointer.read_bytes())["study_id"]
            load_study(sid, store=store)
        else:
            sid = create_study(G0, G0_HASH, store=store)["study_id"]
            with pointer.open("xb") as stream:
                stream.write(canonical_json({"study_id": sid}))
    else:
        sid = json.loads(pointer.read_bytes())["study_id"]
        if args.run:
            run_variant(sid, args.run, seed=args.seed, store=store)
    result = inspect_study(sid, store=store)
    print(
        json.dumps(
            {
                "study_id": sid,
                "protocol_sha256": result["protocol_sha256"],
                "entries": result["entries"],
                "summaries": result["summaries"],
                "comparisons": result["comparisons"],
                "human_review": "pending unless separately recorded",
                "population_scope": "fixed 1000-row sample",
                "llm_requests": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
