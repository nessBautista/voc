#!/usr/bin/env python3
"""Validate host configuration and emit nonsecret records for workspace.sh."""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

DEFAULTS = {
    "auth_mode": "env-file",
    "credential_file": "config/aws.local.env",
    "zenml_port": 8240,
    "mlflow_port": 5003,
    "jupyter_port": 8891,
    "marimo_port": 2722,
}
SECRET_KEYS = frozenset({"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "OPENROUTER_API_KEY"})


class ConfigurationError(ValueError):
    """A configuration error safe to display without secret values."""


def read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, ValueError):
        raise ConfigurationError(f"Cannot read valid TOML from {path.name}.") from None


def validate_dotenv(path: Path, allowed: frozenset[str], *, literal: bool = False, root_env: bool = False) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ConfigurationError("Required secret file is missing or unreadable; rerun setup/configure.") from None
    seen = set()
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if not match or match[1] not in allowed:
            message = "Root .env must contain only JUPYTER_TOKEN; rerun setup/configure to migrate legacy settings." if root_env else "Credential file contains an unsupported entry."
            raise ConfigurationError(f"{message} Line {number}.")
        key, value = match.groups()
        if key in seen:
            raise ConfigurationError(f"Duplicate secret entry at line {number}.")
        seen.add(key)
        quoted = value.startswith(("'", '"'))
        if quoted:
            quote = value[0]
            end = -1
            escaped = False
            for index, character in enumerate(value[1:], 1):
                if character == quote and not escaped:
                    end = index
                    break
                escaped = character == "\\" and not escaped
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith("#")):
                raise ConfigurationError(f"Use single-line dotenv values at line {number}.")
            value = value[1:end]
        else:
            value = value.split(" #", 1)[0].rstrip()
        if "\0" in value or (root_env and not value):
            raise ConfigurationError(f"Invalid or empty secret value at line {number}; rerun setup/configure.")
        if literal and "op://" in value:
            raise ConfigurationError("op:// references require auth_mode = '1password'; no containers were created.")
        if "$" in value and not line.split("=", 1)[1].strip().startswith("'"):
            raise ConfigurationError(f"Single-quote literal dollar signs in dotenv values at line {number}.")
    if root_env and "JUPYTER_TOKEN" not in seen:
        raise ConfigurationError("Set JUPYTER_TOKEN in root .env; rerun setup/configure.")


def load_configuration(root: Path, *, credentials: bool = False) -> dict[str, str]:
    root = root.resolve()
    settings = dict(DEFAULTS)
    for name in ("workspace.toml", "workspace.local.toml"):
        path = root / "config" / name
        if name.endswith(".local.toml") and not path.exists():
            continue
        values = read_toml(path)
        if values.keys() - DEFAULTS.keys():
            raise ConfigurationError(f"Unsupported keys in {name}; rerun setup/configure to migrate legacy settings.")
        settings.update(values)
    if settings["auth_mode"] not in ("env-file", "1password"):
        raise ConfigurationError("auth_mode must be env-file or 1password.")
    ports = [settings[key] for key in DEFAULTS if key.endswith("_port")]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
        raise ConfigurationError("Workspace ports must be integers between 1 and 65535.")
    if len(set(ports)) != len(ports):
        raise ConfigurationError("Workspace ports must be distinct.")
    raw_path = settings["credential_file"]
    if not isinstance(raw_path, str) or not raw_path or any(c in raw_path for c in "\r\n\0\\"):
        raise ConfigurationError("credential_file must be a repository-relative path using forward slashes.")
    path = Path(raw_path)
    if path.suffix != ".env":
        raise ConfigurationError("credential_file must use the .env suffix so Docker excludes it; relocate credentials into config/*.env and update workspace TOML.")
    if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] == "data" or re.match(r"^[A-Za-z]:", raw_path):
        raise ConfigurationError("credential_file must remain inside the repository, outside data; move external credentials into config and update workspace TOML.")
    try:
        resolved = (root / path).resolve()
        relative = resolved.relative_to(root)
    except (ValueError, OSError, RuntimeError):
        raise ConfigurationError("credential_file must not escape the repository through symlinks.") from None
    if not relative.parts or relative.parts[0] == "data":
        raise ConfigurationError("credential_file must be a file outside data.")
    if resolved.suffix != ".env":
        raise ConfigurationError("Credential symlink targets must also use the .env suffix so Docker excludes them; relocate credentials into config/*.env.")
    validate_dotenv(root / ".env", frozenset({"JUPYTER_TOKEN"}), literal=True, root_env=True)
    if credentials:
        validate_dotenv(resolved, SECRET_KEYS, literal=settings["auth_mode"] == "env-file")
    region = ""
    for name in ("storage.toml", "storage.local.toml"):
        path = root / "config" / name
        if name.endswith(".local.toml") and not path.exists():
            continue
        values = read_toml(path)
        region = values.get("region", region)
    if not isinstance(region, str) or not re.fullmatch(r"[A-Za-z0-9-]*", region):
        raise ConfigurationError("Storage region must be a region name or an empty string.")
    return {
        "VOC_WORKSPACE_AUTH_MODE": settings["auth_mode"],
        "VOC_WORKSPACE_CREDENTIAL_FILE": raw_path,
        **{f"VOC_{key.upper()}": str(settings[key]) for key in DEFAULTS if key.endswith("_port")},
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--credentials", action="store_true")
    args = parser.parse_args()
    try:
        records = load_configuration(args.root, credentials=args.credentials)
    except ConfigurationError as error:
        print(f"Workspace configuration: {error}", file=sys.stderr)
        return 1
    for key, value in records.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
