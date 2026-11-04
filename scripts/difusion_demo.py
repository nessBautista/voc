"""Publish and select the saved diffusion comparison for the existing local app.

Run through scripts/difusion.sh. No producer workflow or model is invoked.
"""

import argparse
import json
import subprocess
from pathlib import Path

from voc.publications.store import activate_bundle, current_publication, validate_bundle
from voc_ml.experiments.operations import load_protocol

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "data/experiments/julaug-2026"
DEMO = ROOT / "data/difusion-app"


def bundle_for(system):
    protocol = load_protocol(EXPERIMENT)
    receipt = json.loads((EXPERIMENT / f"{system}-run.json").read_text())
    if (
        receipt["state"] != "completed"
        or receipt["protocol_id"] != protocol["protocol_id"]
    ):
        raise ValueError("The completed run must belong to the frozen experiment")
    candidate = receipt["representation"]["analysis_id"]
    return DEMO / "bundles" / f"{system}-{candidate}", candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "select", "status"])
    parser.add_argument("system", nargs="?", choices=["base", "alternative"])
    args = parser.parse_args()
    if args.command == "select" and not args.system:
        parser.error("select requires base or alternative")
    if args.command == "prepare":
        for system in ("base", "alternative"):
            bundle, candidate = bundle_for(system)
            if not bundle.exists():
                subprocess.run(
                    [
                        "voc",
                        "publication",
                        "build",
                        "--source",
                        "local",
                        "--local-inputs",
                        str(ROOT / "config/analysis/difusion-local-inputs.toml"),
                        "--dataset-version",
                        "b7a61a8b-27d8-46a7-b2ce-67d97268b741",
                        "--analysis",
                        candidate,
                        "--output",
                        str(bundle),
                    ],
                    check=True,
                )
            validate_bundle(bundle)
            # Install both immutable packages; the alternative is active last.
            activate_bundle(bundle, DEMO / "catalog", DEMO / "state")
    elif args.command == "select":
        bundle, _ = bundle_for(args.system)
        activate_bundle(bundle, DEMO / "catalog", DEMO / "state")
    pointer = current_publication(DEMO / "state")
    active = None
    for system in ("base", "alternative"):
        bundle, candidate = bundle_for(system)
        if (
            bundle.exists()
            and validate_bundle(bundle)["publication_id"] == pointer["publication_id"]
        ):
            active = {"system": system, "analysis_id": candidate}
    if active is None:
        raise ValueError("Active publication is not part of this comparison")
    print(
        json.dumps(
            {
                **pointer,
                **active,
                "dashboard": "http://localhost:8092/dashboard/",
                "experiment": str(EXPERIMENT),
                "model_requests": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
