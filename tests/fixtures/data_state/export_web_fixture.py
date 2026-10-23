"""Write the web test fixture for a version 2 package from the shared description.

Run from the repository root with the publications extra installed. The output
is ``apps/voc-app/web/tests/fixtures/example_v2.json``: the selection the
server would report and the query results the dashboard asks for, so the web
tests never hand-write a shape the server does not produce.
"""

import json
import sys
import tempfile
from pathlib import Path

from voc.publications.builder import build_bundle
from voc.publications.queries import query
from voc.publications.store import activate_bundle

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.fixtures.data_state.analysis_example import analysis_description  # noqa: E402

DEFAULT = {}
REQUESTS = [
    ("overview", None),
    ("sources", None),
    ("coverage", None),
    ("freshness", None),
    ("embeddings", None),
    ("summary", DEFAULT),
    ("topic_list", {"sort": "volume"}),
    ("topic_list", {"sort": "negative"}),
    ("timeline", DEFAULT),
    ("platforms", DEFAULT),
    ("topic_detail", {"topic_id": 0}),
    ("timeline", {"topic_id": 0}),
    ("reviews", {"topic_id": 0, "page": 1, "page_size": 6}),
    ("reviews", {"topic_id": 0, "page": 2, "page_size": 6}),
    ("summary", {"platforms": ["google_play"]}),
    ("topic_list", {"sort": "volume", "platforms": ["google_play"]}),
    ("timeline", {"platforms": ["google_play"]}),
    ("platforms", {"platforms": ["google_play"]}),
]


def main(output):
    root = Path(tempfile.mkdtemp())
    bundle = build_bundle(analysis_description(), root / "bundle")
    pointer = activate_bundle(bundle, root / "catalog", root / "state")
    manifest = json.loads(
        (root / "catalog" / pointer["publication_id"] / "manifest.json").read_text()
    )
    selection = {
        **pointer,
        "schema_version": manifest["schema_version"],
        "dataset_id": manifest["dataset_id"],
        "capabilities": manifest["capabilities"],
        "warnings": query(root / "catalog", pointer["publication_id"], "overview")["warnings"],
    }
    results = [
        query(root / "catalog", pointer["publication_id"], kind, params)
        for kind, params in REQUESTS
    ]
    Path(output).write_text(json.dumps({"selection": selection, "results": results}, indent=2, ensure_ascii=False) + "\n")
    print(output, len(results), "results")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "apps/voc-app/web/tests/fixtures/example_v2.json")
