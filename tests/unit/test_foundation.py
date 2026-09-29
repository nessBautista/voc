from voc import __version__
from voc.paths import data_root

def test_configured_root(tmp_path, monkeypatch):
    # Use pytest's temporary directory; monkeypatch restores the environment afterward.
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    # Confirm the app follows the configured data path.
    assert data_root() == tmp_path
    # Confirm the package exposes the expected version.
    assert __version__ == "0.1.0"
