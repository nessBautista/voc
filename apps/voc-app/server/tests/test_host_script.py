"""voc-host formats only blank disks, never leaks secrets, verifies data before activation."""

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
AWS_DIR = REPO / "docker/app/aws"
RELEASE = "0123456789ab"
PUBLICATION = "11111111-2222-4333-8444-555555555555"
VOLUME = "vol-0abc1234def56789a"
STUBS = {
    "lsblk": """print("nvme0n1 vol0root0000000000")
serial = state("serial")
if serial: print("nvme1n1", serial)
""",
    "blkid": """kind = state("fstype")
if "TYPE" in args and kind: print(kind)
elif "UUID" in args and kind: print(state("uuid") or "no-uuid")
elif not kind: sys.exit(2)
""",
    "wipefs": """print(state("signatures") or "", end="")""",
    "mkfs.xfs": """record("mkfs", args)
write("fstype", "xfs"); write("uuid", "1234-UUID")
""",
    "mount": """record("mount", args); write("mounted", "yes")""",
    "findmnt": """sys.exit(0 if state("mounted") else 1)""",
    "chown": """record("chown", args)""",
    "systemctl": """record("systemctl", args)""",
    "dnf": """record("dnf", args)""",
    "curl": """record("curl", args); open(args[args.index("-o") + 1], "wb").write(b"not the pinned compose binary")""",
    "aws": """record("aws", args)
if args[:2] == ["secretsmanager", "get-secret-value"]:
    value = state("secret-" + args[args.index("--secret-id") + 1].rsplit("/", 1)[-1])
    if value is None: sys.exit(1)
    print(value)
elif args[:2] == ["s3", "cp"]:
    source = state_dir / "publication" / args[2].rsplit("/", 1)[-1]
    if not source.exists(): sys.exit(1)
    import shutil; shutil.copy(source, args[3])
elif args[:2] == ["ecr", "get-login-password"]:
    print("token")
""",
    "docker": """record("docker", args)
if args[0] == "login": sys.stdin.read()
if args[0] == "run" and "activate" in args:
    staging = [a for a in args if a.startswith("type=bind,source=") and "target=/input" in a][0].split("source=")[1].split(",")[0]
    root = Path(os.environ["VOC_ROOT"])
    manifest = json.load(open(Path(staging) / "manifest.json"))
    target = root / "srv/voc/catalog" / manifest["publication_id"]
    target.mkdir(parents=True, exist_ok=True)
    import shutil
    for name in ("manifest.json", "publication.duckdb"): shutil.copy(Path(staging) / name, target / name)
    (root / "srv/voc/state/active.json").write_text(json.dumps({"publication_id": manifest["publication_id"]}))
""",
}
PRELUDE = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
state_dir = Path(os.environ["STUB_STATE"])
def state(name):
    path = state_dir / name
    return path.read_text() if path.exists() else None
def write(name, value): (state_dir / name).write_text(value)
def record(name, value):
    with open(state_dir / "calls.jsonl", "a") as log: log.write(json.dumps([name, value]) + "\\n")
"""


class Host:
    def __init__(self, tmp_path):
        self.root = tmp_path / "root"
        self.state = tmp_path / "state"
        self.bin = tmp_path / "bin"
        for path in (
            self.root / "etc/voc",
            self.state,
            self.bin,
            self.state / "publication",
        ):
            path.mkdir(parents=True)
        for name, body in STUBS.items():
            script = self.bin / name
            script.write_text(PRELUDE + body + "\n")
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
        (self.state / "serial").write_text(VOLUME.replace("-", ""))
        release = self.root / "opt/voc/releases" / RELEASE
        (release / "units").mkdir(parents=True)
        for name in (
            "compose.yaml",
            "voc-host",
            "units/voc-secrets.service",
            "units/voc-app.service",
        ):
            (release / name).write_bytes((AWS_DIR / name).read_bytes())
        plugin = self.root / "usr/local/lib/docker/cli-plugins/docker-compose"
        plugin.parent.mkdir(parents=True)
        plugin.write_text("#!/bin/sh\n")
        plugin.chmod(0o755)
        manifest = json.dumps(
            {"publication_id": PUBLICATION, "schema_version": "voc-data-state-v1"}
        ).encode()
        (self.state / "publication/manifest.json").write_bytes(manifest)
        (self.state / "publication/publication.duckdb").write_bytes(b"duckdb bytes")
        self.manifest_sha256 = hashlib.sha256(manifest).hexdigest()
        self.write_release()

    def write_release(self, **overrides):
        values = {
            "VOC_ENVIRONMENT": "pilot",
            "VOC_RELEASE_ID": RELEASE,
            "VOC_IMAGE": "123.dkr.ecr.us-east-2.amazonaws.com/voc-pilot-app@sha256:"
            + "0" * 64,
            "VOC_AWS_REGION": "us-east-2",
            "VOC_PUBLICATIONS_BUCKET": "bucket",
            "VOC_BACKUP_BUCKET": "backups",
            "VOC_LOG_GROUP": "/voc/pilot/host",
            "VOC_DATA_VOLUME_ID": VOLUME,
            "VOC_DEPLOYMENT_BUNDLE_OBJECT": f"deployments/{RELEASE}/bundle.tar.gz",
            "VOC_DEPLOYMENT_BUNDLE_SHA256": "0" * 64,
            "VOC_PUBLICATION_ID": PUBLICATION,
            "VOC_PUBLICATION_MANIFEST_SHA256": self.manifest_sha256,
            "VOC_WEB_AUTH_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:000:secret:voc/pilot/web-auth-abc",
            "VOC_MODEL_PROVIDER_SECRET_ARN": "arn:aws:secretsmanager:us-east-2:000:secret:voc/pilot/model-provider-def",
            "VOC_AUTH_MODE": "cognito",
            "VOC_PUBLIC_ORIGIN": "http://localhost:8091",
            "VOC_COGNITO_ISSUER": "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_X",
            "VOC_COGNITO_DOMAIN": "https://x.auth.us-east-2.amazoncognito.com",
            "VOC_COGNITO_WEB_CLIENT_ID": "web",
            "VOC_COGNITO_NATIVE_CLIENT_ID": "native",
            "VOC_COGNITO_SCOPES": "'openid email voc-api/access'",
            "VOC_PLANNER_MODE": "fixture",
            "VOC_MCP_ENABLED": "false",
            **overrides,
        }
        (self.root / "etc/voc/release.env").write_text(
            "".join(f"{key}={value}\n" for key, value in values.items())
        )

    def run(self, *command, ok=True):
        completed = subprocess.run(
            ["bash", str(AWS_DIR / "voc-host"), *command],
            env={
                "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                "VOC_ROOT": str(self.root),
                "STUB_STATE": str(self.state),
                "HOME": str(self.root),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert (completed.returncode == 0) == ok, completed.stderr + completed.stdout
        return completed

    def calls(self, name):
        log = self.state / "calls.jsonl"
        if not log.exists():
            return []
        return [
            json.loads(line)[1]
            for line in log.read_text().splitlines()
            if json.loads(line)[0] == name
        ]

    def mounted(self):
        (self.state / "mounted").write_text("yes")

    def secret(self, name, value):
        (self.state / f"secret-{name}").write_text(value)


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def test_storage_formats_a_blank_volume_once_and_mounts_by_uuid(host):
    host.run("storage")
    assert len(host.calls("mkfs")) == 1
    fstab = (host.root / "etc/fstab").read_text()
    assert fstab == "UUID=1234-UUID /srv/voc xfs defaults,noatime,nofail 0 2\n"
    assert all(
        (host.root / "srv/voc" / name).is_dir()
        for name in ("catalog", "state", "incoming")
    )
    host.run("storage")
    assert len(host.calls("mkfs")) == 1
    assert (host.root / "etc/fstab").read_text() == fstab


def test_storage_refuses_disks_that_are_not_blank_or_not_attached(host):
    (host.state / "signatures").write_text(
        "DEVICE OFFSET TYPE UUID LABEL\n/dev/nvme1n1 0x438 ext4 ..."
    )
    failed = host.run("storage", ok=False)
    assert "refusing to format" in failed.stderr and not host.calls("mkfs")
    (host.state / "signatures").unlink()
    (host.state / "fstype").write_text("ext4")
    (host.state / "uuid").write_text("EXT4-UUID")
    assert "expected xfs" in host.run("storage", ok=False).stderr
    (host.state / "serial").write_text("vol0someotherdisk000")
    assert "not attached" in host.run("storage", ok=False).stderr
    assert not host.calls("mkfs")


def test_configure_writes_runtime_keys_only_and_set_changes_them(host):
    host.run("configure")
    app_env = (host.root / "etc/voc/app.env").read_text()
    keys = {line.split("=")[0] for line in app_env.splitlines()}
    assert "VOC_COGNITO_ISSUER" in keys and "VOC_PLANNER_MODE" in keys
    assert not any(key.endswith(("_ARN", "_SHA256")) or "BUNDLE" in key for key in keys)
    host.run("set", "VOC_PLANNER_MODE=model")
    assert "VOC_PLANNER_MODE='model'\n" in (host.root / "etc/voc/app.env").read_text()
    assert host.calls("systemctl")[-1] == [
        "restart",
        "voc-secrets.service",
        "voc-app.service",
    ]
    refused = host.run("set", "VOC_COGNITO_WEB_CLIENT_SECRET=nope", ok=False)
    assert "secrets never go here" in refused.stderr
    assert "nope" not in (host.root / "etc/voc/app.env").read_text()


def test_secrets_land_in_a_private_file_or_nowhere(host):
    host.run("configure")
    failed = host.run("secrets", ok=False)
    assert "cannot read secret" in failed.stderr
    assert not (host.root / "run/voc/app.secret.env").exists()
    host.secret("web-auth-abc", json.dumps({"client_secret": "hush-value"}))
    host.run("secrets")
    secret_file = host.root / "run/voc/app.secret.env"
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert secret_file.read_text() == "VOC_COGNITO_WEB_CLIENT_SECRET=hush-value\n"
    host.run("set", "VOC_PLANNER_MODE=model")
    missing = host.run("secrets", ok=False)
    assert "hush-value" not in missing.stderr + missing.stdout
    host.secret("model-provider-def", json.dumps({"api_key": ""}))
    assert "has no value" in host.run("secrets", ok=False).stderr
    host.secret("model-provider-def", json.dumps({"api_key": "provider-key"}))
    host.run("secrets")
    assert "VOC_LLM_API_KEY=provider-key\n" in secret_file.read_text()


def test_publication_is_verified_before_activation_and_installed_once(host):
    host.mounted()
    host.run("storage")
    host.run("install-publication")
    activations = [call for call in host.calls("docker") if "activate" in call]
    assert len(activations) == 1 and "--network" in activations[0]
    assert (host.root / "srv/voc/catalog" / PUBLICATION / "manifest.json").is_file()
    assert not list((host.root / "srv/voc/incoming").iterdir())
    host.run("install-publication")
    assert len([call for call in host.calls("docker") if "activate" in call]) == 1
    assert len([call for call in host.calls("aws") if call[:2] == ["s3", "cp"]]) == 2


def test_wrong_or_missing_publication_activates_nothing(host):
    host.mounted()
    host.run("storage")
    failed = host.run("install-publication", PUBLICATION, "f" * 64, ok=False)
    assert "does not match" in failed.stderr
    assert not any("activate" in call for call in host.calls("docker"))
    assert not list((host.root / "srv/voc/incoming").iterdir())
    (host.state / "publication/publication.duckdb").unlink()
    host.run("install-publication", ok=False)
    assert not (host.root / "srv/voc/state/active.json").exists()


def test_app_starts_only_on_the_data_volume_with_secrets_present(host):
    host.run("configure")
    assert "not mounted" in host.run("app-up", ok=False).stderr
    host.mounted()
    assert "voc-secrets.service" in host.run("app-up", ok=False).stderr
    host.secret("web-auth-abc", json.dumps({"client_secret": "hush-value"}))
    host.run("secrets")
    host.run("app-up")
    up = [call for call in host.calls("docker") if call[:1] == ["compose"]][-1]
    assert "--env-file" in up and up[up.index("--env-file") + 1].endswith(
        "/etc/voc/app.env"
    )
    assert up[-3:] == ["up", "--detach", "--wait"]


def test_bootstrap_runs_every_step_and_is_safe_to_repeat(host):
    host.secret("web-auth-abc", json.dumps({"client_secret": "hush-value"}))
    host.run("bootstrap")
    assert not host.calls("dnf")  # docker already present
    assert len(host.calls("mkfs")) == 1
    systemctl = host.calls("systemctl")
    assert ["enable", "--now", "docker"] in systemctl
    assert ["enable", "voc-secrets.service", "voc-app.service"] in systemctl
    assert ["start", "voc-secrets.service"] in systemctl and [
        "start",
        "voc-app.service",
    ] in systemctl
    assert (host.root / "etc/systemd/system/voc-app.service").is_file()
    host.run("bootstrap")
    assert len(host.calls("mkfs")) == 1
    assert len([call for call in host.calls("docker") if "activate" in call]) == 1


def test_an_unverified_compose_download_is_rejected(host):
    (host.root / "usr/local/lib/docker/cli-plugins/docker-compose").unlink()
    host.run("packages", ok=False)
    assert host.calls("curl")
    assert not (host.root / "usr/local/lib/docker/cli-plugins/docker-compose").exists()


def make_bundle(tmp_path, release, digest):
    """A deployment bundle like bundle.py builds, for the release switch tests."""
    import io
    import runpy
    import tarfile

    builder = runpy.run_path(str(AWS_DIR / "bundle.py"), run_name="bundle")
    result = builder["build"](release, digest, tmp_path / "dist")
    archive = (tmp_path / "dist" / release / "bundle.tar.gz").read_bytes()
    return archive, result["sha256"]


def test_release_switches_image_and_bundle_and_keeps_the_old_release(host, tmp_path):
    host.secret("web-auth-abc", json.dumps({"client_secret": "hush-value"}))
    host.run("bootstrap")
    new_release, new_digest = "fedcba987654", "sha256:" + "1" * 64
    archive, sha = make_bundle(tmp_path, new_release, new_digest)
    (host.state / "publication" / "bundle.tar.gz").write_bytes(archive)
    host.run("release", f"deployments/{new_release}/bundle.tar.gz", sha, new_digest)
    release_env = (host.root / "etc/voc/release.env").read_text()
    assert f"VOC_RELEASE_ID='{new_release}'" in release_env
    assert (
        f"VOC_IMAGE='123.dkr.ecr.us-east-2.amazonaws.com/voc-pilot-app@{new_digest}'"
        in release_env
    )
    app_env = (host.root / "etc/voc/app.env").read_text()
    assert (
        f"VOC_IMAGE='123.dkr.ecr.us-east-2.amazonaws.com/voc-pilot-app@{new_digest}'"
        in app_env
    )
    assert (host.root / "opt/voc/releases" / new_release / "manifest.json").is_file()
    assert (
        host.root / "opt/voc/releases" / RELEASE / "compose.yaml"
    ).is_file()  # rollback material
    restarts = [call for call in host.calls("systemctl") if call[:1] == ["restart"]]
    assert ["restart", "voc-app.service"] in restarts
    assert host.run("verify-release").returncode == 0


def test_release_refuses_bad_digests_and_mismatched_bundles(host, tmp_path):
    host.secret("web-auth-abc", json.dumps({"client_secret": "hush-value"}))
    host.run("bootstrap")
    before = (host.root / "etc/voc/release.env").read_text()
    new_release, new_digest = "fedcba987654", "sha256:" + "1" * 64
    archive, sha = make_bundle(tmp_path, new_release, new_digest)
    (host.state / "publication" / "bundle.tar.gz").write_bytes(archive)
    failed = host.run(
        "release",
        f"deployments/{new_release}/bundle.tar.gz",
        "0" * 64,
        new_digest,
        ok=False,
    )
    assert "digest mismatch" in failed.stderr
    assert not (host.root / "opt/voc/releases" / new_release).exists()
    failed = host.run(
        "release",
        f"deployments/{new_release}/bundle.tar.gz",
        sha,
        "sha256:" + "2" * 64,
        ok=False,
    )
    assert "built for image" in failed.stderr
    assert (host.root / "etc/voc/release.env").read_text() == before
    assert "usage" in host.run("release", ok=False).stderr
