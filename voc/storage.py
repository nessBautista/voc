"""Storage settings and a persistent identity; no AWS requests or tool setup."""

import os
import re
import tempfile
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

from .paths import data_root

DEFAULTS = {
    "artifact_destination": "local",
    "dataset_source": "local",
    "shared_prefix": "voc/datasets",
    "members_prefix": "members",
    "bucket": "",
    "region": "",
    "member_id": "",
}


def installation_id(root):
    """Create once under persistent runtime storage; concurrent callers agree."""
    folder = Path(root) / "storage"
    path = folder / "installation-id"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        # Publish a complete file atomically. Never overwrite an existing ID.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=folder, delete=False) as f:
                temporary = Path(f.name)
                f.write(str(uuid.uuid4()) + "\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass  # Another caller already created the winning ID.
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    value = path.read_text().strip()
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value or parsed.version != 4:
            raise ValueError
    except ValueError as error:
        raise ValueError(
            f"Invalid installation ID at {path}; restore it instead of silently "
            "creating a new artifact namespace."
        ) from error
    return value


def _prefix(value, name):
    value = value.strip("/")
    if not value or any(
        part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part)
        for part in value.split("/")
    ):
        raise ValueError(f"{name} must contain ordinary slash-separated key segments")
    return value


@dataclass(frozen=True)
class StorageSettings:
    artifact_destination: str
    dataset_source: str
    runtime_root: Path
    bucket: str
    region: str
    member_id: str
    shared_prefix: str
    members_prefix: str
    installation_id: str | None

    @property
    def shared_dataset_uri(self):
        return f"s3://{self.bucket}/{self.shared_prefix}" if self.bucket else None

    @property
    def personal_uri(self):
        if self.installation_id is None:
            return None
        return f"s3://{self.bucket}/{self.members_prefix}/{self.member_id}/{self.installation_id}"

    @property
    def zenml_artifact_uri(self):
        if self.artifact_destination == "s3":
            return self.personal_uri + "/zenml-artifacts"
        return str(self.runtime_root / "zenml-artifacts")

    @property
    def mlflow_artifact_uri(self):
        if self.artifact_destination == "s3":
            return self.personal_uri + "/mlflow-artifacts"
        return (self.runtime_root / "mlflow/artifacts").as_uri()

    def describe(self):
        """Non-secret settings for the CLI; these are not active-stack evidence."""
        return {
            "artifact_destination": self.artifact_destination,
            "dataset_source": self.dataset_source,
            "runtime_root": str(self.runtime_root),
            "bucket": self.bucket,
            "region": self.region,
            "member_id": self.member_id,
            "installation_id": self.installation_id,
            "shared_dataset_uri": self.shared_dataset_uri,
            "personal_uri": self.personal_uri,
            "zenml_artifact_uri": self.zenml_artifact_uri,
            "mlflow_artifact_uri": self.mlflow_artifact_uri,
        }


def load_storage(path=None, *, destination=None):
    """Apply defaults, TOML, environment, then an optional CLI destination override.

    A missing default file uses local defaults for standalone library consumers.
    An explicitly supplied file must exist. Only selecting personal S3 storage
    creates an installation ID; loading local defaults has no filesystem effects.
    """
    explicit = path is not None or "VOC_STORAGE_CONFIG" in os.environ
    path = Path(path or os.environ.get("VOC_STORAGE_CONFIG", "config/storage.toml"))
    values = dict(DEFAULTS)
    if explicit or path.exists():
        with path.open("rb") as stream:
            supplied = tomllib.load(stream)
        if set(supplied) - set(DEFAULTS):
            raise ValueError(
                "Unknown storage setting: "
                + ", ".join(sorted(set(supplied) - set(DEFAULTS)))
            )
        values.update(supplied)
    for key, env in {
        "bucket": "AWS_BUCKET",
        "region": "AWS_REGION",
        "member_id": "VOC_MEMBER_ID",
        "artifact_destination": "VOC_ARTIFACT_DESTINATION",
        "dataset_source": "VOC_DATASET_SOURCE",
    }.items():
        if os.environ.get(env):
            values[key] = os.environ[env]
    if destination is not None:
        values["artifact_destination"] = destination
    if any(not isinstance(v, str) for v in values.values()):
        raise ValueError("Storage settings must be strings")
    for key in ("artifact_destination", "dataset_source"):
        if values[key] not in ("local", "s3"):
            raise ValueError(f"{key} must be local or s3")
    shared = _prefix(values["shared_prefix"], "shared_prefix")
    members = _prefix(values["members_prefix"], "members_prefix")
    if (
        shared == members
        or shared.startswith(members + "/")
        or members.startswith(shared + "/")
    ):
        raise ValueError(
            "Shared dataset and personal artifact prefixes must not overlap"
        )
    values.update(shared_prefix=shared, members_prefix=members)
    uses_s3 = "s3" in (values["artifact_destination"], values["dataset_source"])
    if uses_s3:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", values["bucket"]):
            raise ValueError(
                "Set AWS_BUCKET to the bucket name, without s3:// or a path"
            )
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", values["region"]):
            raise ValueError("Set AWS_REGION to a region code, such as us-east-2")
    if values["artifact_destination"] == "s3" and not re.fullmatch(
        r"[A-Za-z0-9_-]+", values["member_id"]
    ):
        raise ValueError("Set VOC_MEMBER_ID using letters, digits, _ or -")
    root = data_root()
    identity = installation_id(root) if values["artifact_destination"] == "s3" else None
    return StorageSettings(**values, runtime_root=root, installation_id=identity)
