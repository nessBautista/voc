#!/usr/bin/env python3
"""Copy a personal Neovim configuration into this workspace, never back to its source."""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path


def copy_configuration(source: Path, root: Path) -> Path:
    source = source.expanduser().resolve()
    destination = root.resolve() / "data/editor/config/nvim"
    overlay = Path("lua/custom/plugins/voc-container.lua")
    if (source / overlay).exists():
        raise ValueError("Source already contains voc-container.lua; choose your original host configuration")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            f"Keep your existing editor configuration: {destination}. "
            "Move it to a backup location before importing another snapshot."
        )
    required = ("init.lua", "lua", "lazy-lock.json")
    if not all((source / name).exists() for name in required):
        raise ValueError("Choose the Neovim directory containing init.lua, lua/, and lazy-lock.json")
    if not (source / "lua").is_dir():
        raise ValueError("The Neovim lua/ entry must be a directory")
    entries = [source / name for name in required]
    if (source / "doc").exists():
        entries.append(source / "doc")
    for entry in entries:
        paths = [entry, *entry.rglob("*")] if entry.is_dir() else [entry]
        if any(path.is_symlink() for path in paths):
            raise ValueError("Configuration entries must be regular files/directories, not symlinks")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".nvim-import-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "nvim"
        staging.mkdir()
        for entry in entries:
            if entry.is_dir():
                shutil.copytree(entry, staging / entry.name)
            else:
                shutil.copy2(entry, staging / entry.name)
        (staging / overlay).parent.mkdir(parents=True, exist_ok=True)
        (staging / overlay).write_text(
            "return require('voc_container')\n", encoding="utf-8"
        )
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Editor configuration appeared during import: {destination}")
        staging.rename(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Host directory containing your Neovim configuration")
    args = parser.parse_args()
    try:
        destination = copy_configuration(args.source, Path(__file__).resolve().parents[1])
    except (OSError, ValueError) as error:
        parser.exit(1, f"Editor setup: {error}\n")
    print(f"Copied configuration to {destination}")
    print('Set editor = "nvim" in config/workspace.local.toml, then run just up.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
