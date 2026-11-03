"""Isolated fitting process. Parent enforces timeout and terminates its process group."""

import json
import os
import resource
import sys
from pathlib import Path
from time import monotonic


def main():
    folder = Path(sys.argv[1])
    request = json.loads((folder / "request.json").read_bytes())
    threads = request["configuration"]["threads"]
    affinity = None
    if hasattr(os, "sched_getaffinity"):
        affinity = sorted(os.sched_getaffinity(0))[:threads]
        os.sched_setaffinity(0, affinity)
    import numpy as np
    import pandas as pd

    from voc.embeddings.shared.contracts import canonical_json

    from .config import TopicConfig
    from .discovery import discover
    from .selection import TopicInput

    inputs = TopicInput(
        pd.read_parquet(folder / "input.parquet"),
        np.load(folder / "vectors.npy", allow_pickle=False),
        request["input"],
    )
    from importlib.metadata import version

    from voc.topics import read_topics

    # Refuse an environment drift before either fit or semantic inference.
    for name, expected in (request.get("expected_software") or {}).items():
        if version(name) != expected:
            raise ValueError(f"Study environment changed: {name}")
    if request.get("expected_encoder") is not None:
        from voc.embeddings.shared.profiles import resolve_profile
        from voc_ml.embeddings.encoding.sentence_encoder import encoder_descriptor

        if encoder_descriptor(resolve_profile()) != request["expected_encoder"]:
            raise ValueError("Study semantic encoder environment changed")
    started = monotonic()
    if request.get("operation") == "representation":
        from .representation import RepresentationConfig, refine

        parent = read_topics(
            request["parent_directory"],
            manifest_sha256=request["parent"]["manifest_sha256"],
        )
        if inputs.info != parent.info["input"] or not inputs.reviews.equals(
            parent.reviews
        ):
            raise ValueError("Refinement input differs from parent")
        topics, report = refine(
            parent,
            RepresentationConfig(**request["representation_config"]),
            Path(request["models_directory"]),
        )
        assignments, coordinates = parent.assignments, parent.coordinates
    else:
        assignments, topics, coordinates, report = discover(
            inputs, TopicConfig(**request["configuration"])
        )
    report["fit_seconds_including_imports"] = monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["peak_worker_rss_bytes"] = int(
        rss * (1 if sys.platform == "darwin" else 1024)
    )
    report["resource_scope"] = (
        "worker process RSS high-water mark, not aggregate child RSS or a memory limit"
    )
    report["cpu_affinity"] = affinity
    assignments.to_parquet(folder / "assignments.parquet", index=False)
    np.save(folder / "coordinates.npy", coordinates, allow_pickle=False)
    (folder / "topics.json").write_bytes(canonical_json(topics))
    (folder / "fit.json").write_bytes(canonical_json(report))


if __name__ == "__main__":
    main()
