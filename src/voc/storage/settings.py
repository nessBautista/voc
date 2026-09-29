"""Storage settings and a persistent identity; no AWS requests or tool setup."""

import os
import re
import tempfile
import tomllib
import uuid
from pathlib import Path

from ..models import StorageSettings
from ..paths import data_root

# --- Default configuration ---
# Used before applying file settings and overrides.
DEFAULTS = {
    "artifact_destination": "local",
    "dataset_source": "personal",
    "shared_prefix": "voc/datasets",
    "members_prefix": "members",
    "bucket": "",
    "region": "",
    "member_id": "",
}


# --- Helper functions used by the loader ---


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


# The leading underscore marks this helper as internal by convention.
def _prefix(value, name):
    value = value.strip("/")
    if not value or any(
        part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part)
        for part in value.split("/")
    ):
        raise ValueError(f"{name} must contain ordinary slash-separated key segments")
    return value


# --- Main configuration loader ---
# path selects a TOML file; * makes destination a keyword-only argument.
def load_storage(path=None, *, destination=None):
    """Apply defaults -> TOML -> environment -> then an optional CLI destination override.

    A missing default file uses local defaults for standalone library consumers.
    An explicitly supplied file must exist. Only selecting personal S3 storage
    creates an installation ID; loading local defaults has no filesystem effects.
    """
    # 1. Choose the file. Explicit paths must exist; the default file is optional.
    explicit = path is not None or "VOC_STORAGE_CONFIG" in os.environ
    path = Path(path or os.environ.get("VOC_STORAGE_CONFIG", "config/storage.toml"))

    # 2. Start with a copy of defaults, then merge recognized TOML settings.
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

    # 3. Nonempty environment values override the defaults and TOML.
    for key, env in {
        "bucket": "AWS_BUCKET",
        "region": "AWS_REGION",
        "member_id": "VOC_MEMBER_ID",
        "artifact_destination": "VOC_ARTIFACT_DESTINATION",
        "dataset_source": "VOC_DATASET_SOURCE",
    }.items():
        if os.environ.get(env):
            values[key] = os.environ[env]

    # 4. An explicit destination overrides only where personal artifacts go.
    if destination is not None:
        values["artifact_destination"] = destination

    # 5. Validate types and allowed storage modes before creating any files.
    if any(not isinstance(v, str) for v in values.values()):
        raise ValueError("Storage settings must be strings")
    # Preserve older configs while naming the dataset source by ownership.
    if values["dataset_source"] == "local":
        values["dataset_source"] = "personal"
    for key, choices in (
        ("artifact_destination", ("local", "s3")),
        ("dataset_source", ("personal", "s3")),
    ):
        if values[key] not in choices:
            raise ValueError(f"{key} must be {' or '.join(choices)}")

    # Keep shared datasets and personal artifacts in separate S3 prefixes.
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

    # S3 needs a bucket and region; personal S3 artifacts also need a member ID.
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

    # 6. Resolve runtime storage; personal S3 mode creates/reuses its installation ID.
    root = data_root()
    identity = installation_id(root) if values["artifact_destination"] == "s3" else None

    # 7. Return the resolved model. **values passes each dictionary item by name.
    return StorageSettings(**values, runtime_root=root, installation_id=identity)
