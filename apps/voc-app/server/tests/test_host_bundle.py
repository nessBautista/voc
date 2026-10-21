"""The deployment bundle is deterministic, complete and never overwrites a release."""

import hashlib
import json
import os
import runpy
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
AWS_DIR = REPO / "docker/app/aws"
bundle = runpy.run_path(str(AWS_DIR / "bundle.py"), run_name="bundle")
RELEASE = "0123456789ab"
DIGEST = "sha256:" + "0" * 64


def test_build_packs_the_reviewed_host_files_with_their_digests(tmp_path):
    result = bundle["build"](RELEASE, DIGEST, tmp_path)
    assert result["key"] == f"deployments/{RELEASE}/bundle.tar.gz"
    archive = tmp_path / RELEASE / "bundle.tar.gz"
    assert result["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as tar:
        members = {member.name: member for member in tar.getmembers()}
        manifest = json.load(tar.extractfile(members["manifest.json"]))
    assert set(members) == {*bundle["FILES"], "manifest.json"}
    assert members["voc-host"].mode == 0o755
    assert members["compose.yaml"].mode == 0o644
    assert manifest["image_digest"] == DIGEST
    for name, digest in manifest["files"].items():
        assert digest == hashlib.sha256((AWS_DIR / name).read_bytes()).hexdigest()
    assert (tmp_path / RELEASE / "bundle.sha256").read_text().split() == [
        result["sha256"],
        "bundle.tar.gz",
    ]


def test_build_is_deterministic(tmp_path):
    first = bundle["build"](RELEASE, DIGEST, tmp_path / "a")
    second = bundle["build"](RELEASE, DIGEST, tmp_path / "b")
    assert first["sha256"] == second["sha256"]
    # The gzip header carries no timestamp, so another day or machine yields the same bytes.
    assert (tmp_path / "a" / RELEASE / "bundle.tar.gz").read_bytes()[
        4:8
    ] == b"\x00\x00\x00\x00"


def test_host_files_contain_no_credentials_or_identifiers():
    for name in bundle["FILES"]:
        text = (AWS_DIR / name).read_text()
        assert "op://" not in text and "AKIA" not in text, name
        assert not any(
            token.isdigit() and len(token) == 12 for token in text.split()
        ), name


def fake_aws(tmp_path, head_status):
    script = tmp_path / "aws"
    script.write_text(
        f"""#!/usr/bin/env python3
import json, sys
log = open({str(tmp_path / "calls.jsonl")!r}, "a")
log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit({head_status} if sys.argv[1] == "s3api" else 0)
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_upload_refuses_an_existing_release_and_copies_two_files(tmp_path):
    bundle["build"](RELEASE, DIGEST, tmp_path / "out")
    with pytest.raises(SystemExit, match="immutable"):
        bundle["upload"](
            RELEASE,
            "bucket",
            "p",
            "us-east-2",
            tmp_path / "out",
            aws=str(fake_aws(tmp_path, 0)),
        )
    (tmp_path / "calls.jsonl").unlink()
    result = bundle["upload"](
        RELEASE,
        "bucket",
        "p",
        "us-east-2",
        tmp_path / "out",
        aws=str(fake_aws(tmp_path, 1)),
    )
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    copies = [call for call in calls if call[:2] == ["s3", "cp"]]
    assert [call[3] for call in copies] == [
        f"s3://bucket/deployments/{RELEASE}/bundle.tar.gz",
        f"s3://bucket/deployments/{RELEASE}/manifest.json",
    ]
    assert all("--profile" in call and "p" in call for call in copies)
    assert result["key"] == f"deployments/{RELEASE}/bundle.tar.gz"


def test_command_line_builds_into_the_requested_directory(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(AWS_DIR / "bundle.py"),
            "build",
            "--release-id",
            RELEASE,
            "--image-digest",
            DIGEST,
            "--out",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert json.loads(completed.stdout)["release_id"] == RELEASE
    assert (tmp_path / RELEASE / "manifest.json").is_file()
