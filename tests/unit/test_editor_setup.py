"""Personal editor imports must not replace user work or follow external files."""
import runpy
from pathlib import Path

import pytest

copy_configuration = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/editor.py")
)["copy_configuration"]


@pytest.fixture
def source(tmp_path):
    config = tmp_path / "personal"
    (config / "lua").mkdir(parents=True)
    (config / "init.lua").write_text("return {}\n")
    (config / "lazy-lock.json").write_text("{}\n")
    return config


def test_existing_configuration_is_preserved(source, tmp_path):
    root = tmp_path / "workspace"
    existing = root / "data/editor/config/nvim"
    existing.mkdir(parents=True)
    (existing / "init.lua").write_text("local personal_edit = true\n")
    with pytest.raises(FileExistsError, match="Keep your existing"):
        copy_configuration(source, root)
    assert (existing / "init.lua").read_text() == "local personal_edit = true\n"


def test_import_rejects_external_symlinks_without_partial_snapshot(source, tmp_path):
    external = tmp_path / "private.lua"
    external.write_text("private content\n")
    (source / "lua/external.lua").symlink_to(external)
    root = tmp_path / "workspace"
    with pytest.raises(ValueError, match="symlinks"):
        copy_configuration(source, root)
    assert not (root / "data/editor/config/nvim").exists()


def test_incomplete_source_does_not_install_configuration(source, tmp_path):
    (source / "lazy-lock.json").unlink()
    root = tmp_path / "workspace"
    with pytest.raises(ValueError, match="lazy-lock.json"):
        copy_configuration(source, root)
    assert not (root / "data/editor/config/nvim").exists()
