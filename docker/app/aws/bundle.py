"""Build and upload the versioned deployment bundle the VOC host installs.

Mac side, standard library only. `build` writes dist/aws/<release-id>/ with
bundle.tar.gz, its SHA-256 and a manifest naming every file's digest and the
expected image digest. `upload` copies the two files to the private bucket
under deployments/<release-id>/ and refuses to overwrite an existing release.
"""

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = (
    "compose.yaml",
    "voc-host",
    "units/voc-secrets.service",
    "units/voc-app.service",
    "units/voc-autostop.service",
    "units/voc-autostop.timer",
)
SCHEMA = "voc-deployment-v1"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def build(release_id, image_digest, out):
    """Pack the reviewed host files deterministically; same inputs, same bytes."""
    target = Path(out) / release_id
    target.mkdir(parents=True, exist_ok=True)
    contents = {name: (HERE / name).read_bytes() for name in FILES}
    manifest = {
        "schema_version": SCHEMA,
        "release_id": release_id,
        "image_digest": image_digest,
        "files": {name: sha256(data) for name, data in contents.items()},
    }
    encoded = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    buffer = io.BytesIO()
    # A fixed gzip timestamp keeps the archive bytes identical across machines and days.
    with (
        gzip.GzipFile(
            fileobj=buffer, mode="wb", compresslevel=9, mtime=0
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as tar,
    ):
        for name, data in [*sorted(contents.items()), ("manifest.json", encoded)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = 0o755 if name == "voc-host" else 0o644
            tar.addfile(info, io.BytesIO(data))
    bundle = buffer.getvalue()
    (target / "bundle.tar.gz").write_bytes(bundle)
    (target / "manifest.json").write_bytes(encoded)
    digest = sha256(bundle)
    (target / "bundle.sha256").write_text(f"{digest}  bundle.tar.gz\n")
    return {
        "release_id": release_id,
        "key": f"deployments/{release_id}/bundle.tar.gz",
        "sha256": digest,
        "image_digest": image_digest,
    }


def upload(release_id, bucket, profile, region, out, aws="aws"):
    target = Path(out) / release_id
    key = f"deployments/{release_id}/bundle.tar.gz"
    common = ["--profile", profile, "--region", region]
    exists = subprocess.run(
        [aws, "s3api", "head-object", "--bucket", bucket, "--key", key, *common],
        capture_output=True,
        text=True,
    )
    if exists.returncode == 0:
        raise SystemExit(f"{key} already exists; releases are immutable")
    for name in ("bundle.tar.gz", "manifest.json"):
        subprocess.run(
            [
                aws,
                "s3",
                "cp",
                str(target / name),
                f"s3://{bucket}/deployments/{release_id}/{name}",
                *common,
            ],
            check=True,
        )
    return {
        "key": key,
        "sha256": (target / "bundle.sha256").read_text().split()[0],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "upload"])
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--image-digest")
    parser.add_argument("--bucket")
    parser.add_argument("--profile", default="voc-deploy")
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--out", default="dist/aws")
    arguments = parser.parse_args(argv)
    if arguments.action == "build":
        if not arguments.image_digest or not arguments.image_digest.startswith(
            "sha256:"
        ):
            parser.error("--image-digest must be the ECR sha256 digest")
        result = build(arguments.release_id, arguments.image_digest, arguments.out)
    else:
        if not arguments.bucket:
            parser.error("--bucket is required for upload")
        result = upload(
            arguments.release_id,
            arguments.bucket,
            arguments.profile,
            arguments.region,
            arguments.out,
        )
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
