"""Create private settings or migrate legacy .env settings without host Python."""

import argparse
import json
import os
import re
import secrets
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_KEYS = {
    "auth_mode", "credential_file", "zenml_port", "mlflow_port",
    "jupyter_port", "marimo_port", "editor",
}
STORAGE_KEYS = {
    "bucket", "region", "member_id", "dataset_source", "artifact_destination",
    "shared_prefix", "members_prefix", "embeddings_prefix",
}
LEGACY = {
    "AWS_BUCKET": ("storage", "bucket"),
    "AWS_REGION": ("storage", "region"),
    "VOC_MEMBER_ID": ("storage", "member_id"),
    "VOC_DATASET_SOURCE": ("storage", "dataset_source"),
    "VOC_ARTIFACT_DESTINATION": ("storage", "artifact_destination"),
    "VOC_EMBEDDINGS_PREFIX": ("storage", "embeddings_prefix"),
    "VOC_AWS_AUTH_MODE": ("workspace", "auth_mode"),
    "VOC_AWS_ENV_FILE": ("workspace", "credential_file"),
    "VOC_ZENML_PORT": ("workspace", "zenml_port"),
    "VOC_MLFLOW_PORT": ("workspace", "mlflow_port"),
    "VOC_JUPYTER_PORT": ("workspace", "jupyter_port"),
    "VOC_MARIMO_PORT": ("workspace", "marimo_port"),
}


def safe_path(root, relative):
    """Reject links and paths outside this checkout, including protected data."""
    path = Path(relative)
    if (
        not relative or path.is_absolute() or ".." in path.parts
        or not path.parts or path.parts[0] == "data"
        or "\\" in relative or "\n" in relative or "\r" in relative
        or re.match(r"^[A-Za-z]:", relative)
    ):
        raise ValueError(
            "credential_file must be a repository-relative path outside data/; "
            "relocate private credentials into config/ and update the selection before retrying"
        )
    target = root / path
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("Refusing symlink at " + str(part.relative_to(root)))
        if part != target and part.exists() and not part.is_dir():
            raise ValueError("Expected a directory at " + str(part.relative_to(root)))
    if target.exists() and not target.is_file():
        raise ValueError("Expected a regular file at " + relative)
    return target


def load_toml(path, allowed):
    if not path.exists():
        return {}
    try:
        values = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeError):
        raise ValueError("Invalid TOML in " + path.name) from None
    if values.keys() - allowed:
        raise ValueError("Unknown settings in " + path.name)
    for key, value in values.items():
        expected = int if key.endswith("_port") else str
        if type(value) is not expected:
            raise ValueError("Invalid type for " + key + " in " + path.name)
    return values


def legacy_value(raw, key):
    """Parse a literal dotenv value, never evaluate shell syntax/interpolation."""
    raw = raw.strip()
    if raw.startswith(("'", '"')):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end < 0 or (raw[end + 1:].strip() and not raw[end + 1:].lstrip().startswith("#")):
            raise ValueError("Unsupported quoted legacy assignment: " + key)
        value = raw[1:end]
    else:
        value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
    if any(char in value for char in ("$", "`", "\\", "\r", "\n")):
        raise ValueError("Unsupported interpolation or escaping in legacy assignment: " + key)
    return value


def split_env(content):
    """Retain token/comment bytes while extracting recognized nonsecret settings."""
    retained, migrated, seen = [], {}, set()
    try:
        lines = content.decode("utf-8").splitlines(keepends=True)
    except UnicodeError:
        raise ValueError("Invalid encoding in .env") from None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            retained.append(line)
            continue
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*?)(?:\r?\n)?", line)
        if not match:
            raise ValueError("Unsupported assignment in .env")
        key, raw = match.groups()
        if key in seen:
            raise ValueError("Duplicate assignment in .env: " + key)
        seen.add(key)
        if key == "JUPYTER_TOKEN":
            retained.append(line)
        elif key in LEGACY or key in ("COMPOSE_PROJECT_NAME", "VOC_DASHBOARD_PORT"):
            value = legacy_value(raw, key)
            if key == "COMPOSE_PROJECT_NAME" and value != "voc-main":
                raise ValueError("COMPOSE_PROJECT_NAME must select voc-main")
            migrated[key] = value
        else:
            raise ValueError("Unknown assignment in .env: " + key)
    return "".join(retained).encode("utf-8"), migrated


def validate(workspace, storage):
    """Validate resolved nonsecret settings before creating any private files."""
    if workspace.get("auth_mode") not in ("env-file", "1password"):
        raise ValueError("Invalid auth_mode in workspace TOML")
    editor = workspace.get("editor", "none")
    if type(editor) is not str or editor not in ("none", "nvim"):
        raise ValueError("editor in workspace TOML must be none or nvim")
    if "credential_file" not in workspace:
        raise ValueError("Missing credential_file in workspace TOML")
    ports = []
    for key in sorted(key for key in WORKSPACE_KEYS if key.endswith("_port")):
        value = workspace.get(key)
        if type(value) is not int or not 1 <= value <= 65535:
            raise ValueError(key + " in workspace TOML must be an integer from 1 to 65535")
        ports.append(value)
    if len(set(ports)) != len(ports):
        raise ValueError("Ports in workspace TOML must be distinct")
    if any(not isinstance(value, str) for value in storage.values()):
        raise ValueError("Storage TOML settings must be strings")
    if storage.get("dataset_source") not in ("local", "personal", "s3"):
        raise ValueError("Invalid dataset_source in storage TOML")
    if storage.get("artifact_destination") not in ("local", "s3"):
        raise ValueError("Invalid artifact_destination in storage TOML")
    prefixes = []
    for key in ("shared_prefix", "members_prefix", "embeddings_prefix"):
        prefix = storage.get(key, "").strip("/")
        if not prefix or any(
            part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part)
            for part in prefix.split("/")
        ):
            raise ValueError("Invalid " + key + " in storage TOML")
        prefixes.append(prefix)
    for index, left in enumerate(prefixes):
        for right in prefixes[index + 1:]:
            if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
                raise ValueError("Storage TOML prefixes must not overlap")
    if "s3" in (storage["dataset_source"], storage["artifact_destination"]):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", storage.get("bucket", "")):
            raise ValueError("Invalid bucket in storage TOML")
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", storage.get("region", "")):
            raise ValueError("Invalid region in storage TOML")
    if storage["artifact_destination"] == "s3" and not re.fullmatch(
        r"[A-Za-z0-9_-]+", storage.get("member_id", "")
    ):
        raise ValueError("Invalid member_id in storage TOML")


def publish(path, content, previous):
    """Publish a complete private file, without overwriting unexpected changes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".voc-config-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError("Refusing symlink at " + path.name)
        if previous is None:
            os.link(temporary, path)
        else:
            if path.read_bytes() != previous:
                raise ValueError("Configuration changed during setup: " + path.name)
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def configure(root, member):
    root = Path(root).resolve()
    paths = {
        name: safe_path(root, name)
        for name in (
            ".env", "config/workspace.toml", "config/storage.toml",
            "config/workspace.local.toml", "config/storage.local.toml",
        )
    }
    originals = {name: path.read_bytes() if path.exists() else None for name, path in paths.items()}
    base, local = {}, {}
    for group, allowed in (("workspace", WORKSPACE_KEYS), ("storage", STORAGE_KEYS)):
        base[group] = load_toml(paths[f"config/{group}.toml"], allowed)
        local[group] = load_toml(paths[f"config/{group}.local.toml"], allowed)
    existing_env = originals[".env"]
    if existing_env is None:
        env = ("# Private notebook token; nonsecret settings belong in config/*.toml.\n"
               "JUPYTER_TOKEN=" + secrets.token_hex(24) + "\n").encode()
        migrated = {}
    else:
        env, migrated = split_env(existing_env)
    additions = {"workspace": {}, "storage": {}}
    for legacy, value in migrated.items():
        if legacy not in LEGACY:
            continue
        group, key = LEGACY[legacy]
        if key.endswith("_port"):
            if not re.fullmatch(r"[0-9]+", value):
                raise ValueError("Invalid legacy port assignment: " + legacy)
            value = int(value)
        if key in local[group] and local[group][key] != value:
            raise ValueError("Conflicting " + key + " in config/" + group + ".local.toml")
        if key not in local[group]:
            additions[group][key] = value
    # Existing installations retain their effective member, including --member personal.
    if (
        originals["config/storage.local.toml"] is None
        and "member_id" not in additions["storage"]
        and existing_env is None
        and base["storage"].get("member_id", "personal") == "personal"
    ):
        value = re.sub(r"[^a-z0-9_-]+", "-", member.lower()).strip("-_")[:50] or "teammate"
        additions["storage"]["member_id"] = value
    resolved = {group: base[group] | local[group] | additions[group] for group in base}
    validate(resolved["workspace"], resolved["storage"])
    credential_name = resolved["workspace"]["credential_file"]
    if not credential_name.endswith(".env"):
        raise ValueError(
            "credential_file must end in .env to stay outside Docker build context; "
            "relocate private credentials into config/ with that suffix before retrying"
        )
    credential = safe_path(root, credential_name)
    if credential in paths.values() or credential_name == ".env.before-toml":
        raise ValueError("credential_file must not select another configuration file")
    credential_content = None
    if not credential.exists():
        credential_content = (root / "config/aws.local.env.example").read_bytes()
    backup = None
    if migrated:
        backup = safe_path(root, ".env.before-toml")
        if backup.exists() and backup.read_bytes() != existing_env:
            raise ValueError("Existing .env.before-toml differs; preserve it before retrying migration")
        if backup.exists() and backup.stat().st_mode & 0o077:
            raise ValueError("Make .env.before-toml private (mode 0600) before retrying migration")
    plans = []
    for group in ("workspace", "storage"):
        name = f"config/{group}.local.toml"
        original = originals[name]
        content = original if original is not None else b"# Personal overrides; omitted settings inherit the tracked TOML defaults.\n"
        if additions[group]:
            if content and not content.endswith(b"\n"):
                content += b"\n"
            content += "".join(
                key + " = " + json.dumps(value, ensure_ascii=True) + "\n"
                for key, value in additions[group].items()
            ).encode()
        plans.append((name, paths[name], content, original))
    if credential_content is not None:
        plans.append((credential_name, credential, credential_content, None))
    # All parsing, conflict checks, template reads and path checks precede mutation.
    # Backup is exclusive and can be reused only for an identical interrupted migration.
    if backup is not None and not backup.exists():
        publish(backup, existing_env, None)
    plans.append((".env", paths[".env"], env, existing_env))
    result = {}
    for name, path, content, original in plans:
        changed = original != content
        if changed:
            publish(path, content, original)
        result[name] = changed
        print(("Updated " if changed else "Preserved existing ") + name)
    if credential_content is None:
        result[credential_name] = False
        print("Preserved selected credential_file from workspace TOML")
    if "VOC_DASHBOARD_PORT" in migrated:
        print("Retired VOC_DASHBOARD_PORT; the unused dashboard setting has no replacement.")
    print("Edit the credential_file selected in config/workspace.toml or workspace.local.toml.")
    print("Leave credential keys empty for local work. Review config/storage.local.toml before using S3.")
    print("Then run just up. Open notebooks with just marimo or just jupyter.")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--member", required=True)
    args = parser.parse_args()
    try:
        configure(ROOT, args.member)
    except (ValueError, OSError) as error:
        # OSError may include user-controlled paths, but never secret file contents.
        parser.exit(1, "Configuration failed: " + str(error) + "\n")
