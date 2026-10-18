"""Pipeline identity tracks shared code and the Operator dependency environment."""

import pytest

from voc_ml import identity


@pytest.fixture
def project(tmp_path, monkeypatch):
    files = {
        "src/voc/example.py": "value = 1\n",
        "src/voc_ml/identity.py": "# fixture module location\n",
        "pyproject.toml": "# shared package\n",
        "docker/operator/pyproject.toml": "# operator environment\n",
        "docker/operator/uv.lock": "# resolved dependencies\n",
    }
    for name, contents in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    monkeypatch.setattr(identity, "__file__", str(tmp_path / "src/voc_ml/identity.py"))
    return tmp_path


@pytest.mark.parametrize("changed_file", [
    "src/voc/example.py",
    "pyproject.toml",
    "docker/operator/pyproject.toml",
    "docker/operator/uv.lock",
])
def test_code_or_dependency_changes_invalidate_pipeline_identity(project, changed_file):
    before = identity.code_identity()
    assert identity.code_identity() == before
    path = project / changed_file
    path.write_text(path.read_text() + "# changed\n")
    assert identity.code_identity() != before


def test_missing_operator_lock_cannot_silently_produce_an_identity(project):
    (project / "docker/operator/uv.lock").unlink()
    with pytest.raises(FileNotFoundError):
        identity.code_identity()
