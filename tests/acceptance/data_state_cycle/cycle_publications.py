"""Prepare acceptance publications in the Operator; never change its real selection."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from runpy import run_path
from uuid import uuid4

import pandas as pd

from voc.models import Snapshot
from voc.publications.builder import build_bundle
from voc.publications.exporter import describe_dataset
from voc.publications.queries import query
from voc.publications.store import (
    activate_bundle,
    current_publication,
    file_sha256,
    validate_bundle,
)

REPO = Path(__file__).resolve().parents[3]
KINDS = ("overview", "sources", "coverage", "freshness", "embeddings")


def prepare(root):
    """Create A/B in a new directory; an interrupted setup is never silently reused."""
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Acceptance directory must be empty")
    root.mkdir(parents=True, exist_ok=True)
    (root / "state").mkdir()
    # The app runs as uid 10001; preparation runs as root in the Operator.
    if os.geteuid() == 0:
        os.chown(root / "state", 10001, 10001)
    examples = run_path(str(REPO / "tests/fixtures/data_state/example.py"))
    info = {"format": "voc-app-cycle-v1", "namespace": str(uuid4()), "publications": {}}
    for label in ("a", "b"):
        # Use the real exporter for counts, with explicitly fictional provenance.
        example = examples[f"example_{label}"]()
        description = describe_dataset(
            Snapshot(
                pd.DataFrame(example["records"]),
                {
                    "source": "fixture",
                    "dataset_id": example["dataset_id"],
                    "source_version": example["source_version"],
                    "row_count": len(example["records"]),
                },
            )
        )
        description["provenance"] = deepcopy(example["provenance"])
        for source in description["sources"]:
            source.update(example["collection"][source["platform"]])
        bundle = build_bundle(description, root / "bundles" / label)
        selected = activate_bundle(bundle, root / "catalog", root / "state")
        info["publications"][label] = baseline(root, selected["publication_id"])
    # Begin at A even though both immutable packages are installed.
    activate_bundle(root / "bundles/a", root / "catalog", root / "state")
    (root / "state/cycle.json").write_text(json.dumps(info, indent=2))
    return info


def baseline(root, identity):
    """Keep expected results and exact package digests outside the immutable bundle."""
    package = root / "catalog" / identity
    return {
        "publication_id": identity,
        "files": {
            name: file_sha256(package / name)
            for name in ("manifest.json", "publication.duckdb")
        },
        "results": {kind: query(root / "catalog", identity, kind) for kind in KINDS},
    }


def verify_prepared(root):
    """Resume only a complete, recognized A/B preparation, preserving its identities."""
    info = json.loads((root / "state/cycle.json").read_text())
    if info.get("format") != "voc-app-cycle-v1" or set(
        info.get("publications", {})
    ) != {"a", "b"}:
        raise ValueError("Not a complete synthetic A/B preparation")
    for label, expected in info["publications"].items():
        manifest = validate_bundle(root / "bundles" / label)
        if manifest["publication_id"] != expected["publication_id"]:
            raise ValueError("Prepared identity differs")
        if baseline(root, expected["publication_id"]) != expected:
            raise ValueError("Prepared package or expected results changed")
        if expected["results"]["overview"]["data"]["source_kind"] != "fixture":
            raise ValueError("Initial acceptance preparation must be synthetic")
    selected = current_publication(root / "state")
    if selected["publication_id"] not in {
        item["publication_id"] for item in info["publications"].values()
    }:
        raise ValueError("Unexpected selection in prepared state")
    if (
        file_sha256(root / "catalog" / selected["publication_id"] / "manifest.json")
        != selected["manifest_sha256"]
    ):
        raise ValueError("Prepared selection digest differs")
    return info


def select(root, label, real_catalog=None, real_state=None):
    """Activate only in the dedicated test state; the real catalog stays read-only."""
    info_path = root / "state/cycle.json"
    info = json.loads(info_path.read_text())
    if info["format"] != "voc-app-cycle-v1":
        raise ValueError("Not an acceptance directory")
    if label == "real":
        selected = current_publication(real_state)
        source = Path(real_catalog) / selected["publication_id"]
        if file_sha256(source / "manifest.json") != selected["manifest_sha256"]:
            raise ValueError("Real selection no longer matches its manifest")
        validate_bundle(source)
        activate_bundle(source, root / "catalog", root / "state")
        info["publications"]["real"] = baseline(root, selected["publication_id"])
        info_path.write_text(json.dumps(info, indent=2))
    else:
        activate_bundle(root / "bundles" / label, root / "catalog", root / "state")
    if label == "b":
        reject_invalid(root)
    return current_publication(root / "state")


def reject_invalid(root):
    """Malformed test copies must never replace the active B selection."""
    pointer = (root / "state/active.json").read_bytes()
    for fault in ("checksum", "schema"):
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            bundle = Path(temporary) / "invalid"
            shutil.copytree(root / "bundles/b", bundle)
            if fault == "checksum":
                with (bundle / "publication.duckdb").open("ab") as stream:
                    stream.write(b"invalid")
            else:
                path = bundle / "manifest.json"
                manifest = json.loads(path.read_text())
                manifest["schema_version"] = "future-unsupported"
                path.write_text(json.dumps(manifest))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "voc.cli",
                    "publication",
                    "activate",
                    "--bundle",
                    str(bundle),
                    "--catalog",
                    str(root / "catalog"),
                    "--state",
                    str(root / "state"),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if result.returncode == 0 or "Error:" not in result.stderr:
                raise RuntimeError("Invalid publication was not rejected by the CLI")
            if (root / "state/active.json").read_bytes() != pointer:
                raise RuntimeError("Rejected publication changed the active selection")


if __name__ == "__main__":
    root = REPO / "data/app-cycle"
    action = sys.argv[1]
    if action == "prepare":
        result = prepare(root)
    elif action == "verify":
        result = verify_prepared(root)
    elif action in ("a", "b", "real"):
        result = select(
            root, action, REPO / "data/voc-app/catalog", REPO / "data/voc-app/state"
        )
    else:
        raise SystemExit("Choose prepare, verify, a, b or real")
    print(
        json.dumps(
            {
                "action": action,
                "publication_id": current_publication(root / "state")["publication_id"],
            }
        )
    )
