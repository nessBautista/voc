"""Local model configuration must not execute custom Python (GHSA-jhr6-gm9c-rqjv)."""

import importlib.util
import json

import pytest

from voc.embeddings.profiles import resolve_profile
from voc_ml.embeddings.sentence_encoder import SentenceEncoder


@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="Requires the ml dependency group",
)
def test_encoder_rejects_local_custom_code_before_import(tmp_path, monkeypatch):
    import huggingface_hub
    import transformers.dynamic_module_utils as dynamic_modules

    monkeypatch.setattr(
        dynamic_modules, "HF_MODULES_CACHE", str(tmp_path / "modules-cache")
    )

    profile = resolve_profile("minilm-verbatim-v1")
    snapshot = tmp_path / profile.revision
    snapshot.mkdir()
    sentinel = tmp_path / "custom-code-executed"
    (snapshot / "modules.json").write_text(
        json.dumps(
            [{"idx": 0, "name": "0", "path": "", "type": "modeling_probe.Probe"}]
        )
    )
    # Harmless import side effect detects execution even if model loading later fails.
    (snapshot / "modeling_probe.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed')\n"
        "class Probe:\n"
        "    @classmethod\n"
        "    def load(cls, *args, **kwargs):\n"
        "        raise RuntimeError('Custom model code executed')\n"
    )
    monkeypatch.setattr(
        huggingface_hub, "snapshot_download", lambda **kwargs: str(snapshot)
    )
    # Exercise the production adapter and the real dependency loader, without downloads.
    try:
        with pytest.raises(ValueError, match="trust_remote_code"):
            SentenceEncoder(profile, tmp_path, local_files_only=True)
    finally:
        assert not sentinel.exists(), "Model configuration executed custom Python"
