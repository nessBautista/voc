#!/usr/bin/env python3
"""Verify the installed GUI, helper, release identity and stdio discovery."""

import argparse
import json
import sys
from pathlib import Path

from package import EXECUTABLES, check_bundle, compare_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "app", type=Path, nargs="?", default=Path("/Applications/VOCDataState.app")
    )
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Run on the Mac.")
    expected = json.loads(args.manifest.read_text())
    if expected.get("schema_version") != "voc-desktop-release-v1":
        parser.error("Unsupported release manifest.")
    observed = check_bundle(args.app)
    compare_manifest(observed, expected)
    print(
        json.dumps(
            {
                "installed_app": str(args.app.resolve()),
                "version": observed["version"],
                "build": observed["build"],
                "architecture": observed["architecture"],
                "executable_sha256": {
                    name: observed["files"][name]["sha256"] for name in EXECUTABLES
                },
                "release_manifest_matches": True,
                "mcp": observed["mcp_probe"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
