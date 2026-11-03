"""Owned whole-operation entry point. Reuse the ZenML/MLflow topic workflow."""

import os
import subprocess
import sys


def main():
    # Descendant model workers stay in this session so cancellation owns the whole tree.
    if os.getpid() != os.getpgrp() or os.getsid(0) != os.getpid():
        raise RuntimeError("Corpus workflow requires a dedicated session")
    os.environ["VOC_TOPIC_SUPERVISED_GROUP"] = str(os.getpgrp())
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])
    from .store import TopicRunStore
    from .workflow import execute_workflow

    store = TopicRunStore(sys.argv[2])
    result = execute_workflow(sys.argv[1], store=store)
    # Consumer validation occurs in another process with only the portable API.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from voc.topics import read_topics; "
            "read_topics(sys.argv[1], manifest_sha256=sys.argv[2])",
            result["result"]["directory"],
            result["result"]["manifest_sha256"],
        ],
        check=True,
    )
    store.update(sys.argv[1], separate_process_read="passed")


if __name__ == "__main__":
    main()
