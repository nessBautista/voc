from voc_dev.notebooks import create_notebook, editor_url
import pytest

def test_new_and_existing_notebook(tmp_path):
    path, created = create_notebook(tmp_path, "alice/hello", "jupyter")
    assert created and path == "alice/hello.ipynb"
    original = (tmp_path / path).read_text()
    assert create_notebook(tmp_path, "alice/hello", "jupyter") == (path, False)
    assert (tmp_path / path).read_text() == original
    with pytest.raises(ValueError):
        create_notebook(tmp_path, "../escape", "jupyter")
    assert "8890/lab/tree/alice/hello.ipynb" in editor_url("jupyter", 8890, path)
