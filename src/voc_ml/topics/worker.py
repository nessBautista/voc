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
    started = monotonic()
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
