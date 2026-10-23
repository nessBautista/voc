"""Saved enriched results work through CLI/HTTP without a provider connection."""

import json
import subprocess
import sys

from click.testing import CliRunner

from tests.unit.test_analysis_store import candidate as candidate  # noqa: PLC0414
from tests.unit.test_topic_interpretation import setup as setup  # noqa: PLC0414
from voc.analysis.queries import query
from voc.analysis.store import read_analysis
from voc.cli import main
from voc_ml.analysis import interpretation as workflow


def test_cli_parity_and_core_reader_has_no_model_imports(setup):
    parent, config, _, _ = setup
    result = workflow.interpret(parent.manifest.analysis_id, [0, 1], config, live=True)
    assert result["state"] == "completed", result
    aid = result["analysis_id"]
    cli = CliRunner().invoke(
        main, ["analysis", "query", aid, "--kind", "topics", "--json"]
    )
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.stdout) == query(aid, "topics")
    script = """
import sys
from voc.analysis.store import read_analysis
from voc.analysis.queries import query
saved = read_analysis(sys.argv[1])
assert len(saved.interpretations) == 2
assert query(sys.argv[1], 'topics')['state'] == 'ok'
assert not any(m in sys.modules for m in ('voc_ml', 'openai', 'torch', 'bertopic', 'mlflow', 'zenml'))
"""
    process = subprocess.run(
        [sys.executable, "-c", script, aid], capture_output=True, text=True, check=False
    )
    assert process.returncode == 0, process.stderr


def test_later_representation_keeps_generated_wording_and_artifacts(setup, monkeypatch):
    from unittest.mock import Mock

    from voc_ml.analysis import representation

    parent, config, _, _ = setup
    enriched = workflow.interpret(parent.manifest.analysis_id, [0], config, live=True)
    monkeypatch.setattr(
        representation, "_start_tracking", lambda *a: (Mock(), "tracking")
    )
    monkeypatch.setattr(representation, "_finish_tracking", Mock())
    # This test exercises propagation, not KeyBERT itself (tested separately).
    monkeypatch.setattr(
        representation,
        "refine",
        lambda saved, *a, **kw: (saved.topics, {"fixture": True}),
    )
    later = representation.execute_representation(
        enriched["analysis_id"], "config/analysis/representation.toml"
    )
    assert later["state"] == "completed", later
    assert (
        read_analysis(later["analysis_id"]).interpretations
        == read_analysis(enriched["analysis_id"]).interpretations
    )
