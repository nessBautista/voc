"""CLI and Python queries expose identical JSON-safe results without inference."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from click.testing import CliRunner

from tests.unit.test_analysis_store import candidate as candidate  # noqa: PLC0414
from voc.analysis import queries
from voc.analysis.store import write_analysis
from voc.cli import main


@pytest.fixture
def analysis(candidate):
    return write_analysis(**candidate)


def invoke(*args):
    return CliRunner().invoke(main, ["analysis", *args, "--json"])


@pytest.mark.parametrize("kind", queries.KINDS)
def test_cli_matches_shared_queries(analysis, kind):
    result = invoke("query", analysis.analysis_id, "--kind", kind)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert json.loads(result.stdout) == queries.query(analysis.analysis_id, kind)


def test_list_inspect_and_filtered_pagination(analysis):
    assert json.loads(invoke("list").stdout) == queries.list_candidates()
    assert json.loads(
        invoke("inspect", analysis.analysis_id).stdout
    ) == queries.inspect_analysis(analysis.analysis_id)
    result = invoke(
        "query",
        analysis.analysis_id,
        "--kind",
        "reviews",
        "--topic-id",
        "0",
        "--limit",
        "1",
        "--offset",
        "1",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == queries.reviews(
        analysis.analysis_id, topic_id=0, limit=1, offset=1
    )


@pytest.mark.parametrize(
    "args",
    [
        ["query"],
        ["unknown"],
        ["query", "../escape"],
        ["inspect", str(uuid4())],
        ["query", "ID", "--kind", "drop table"],
        ["query", "ID", "--bogus", "value"],
        ["query", "ID", "--limit", "0"],
        ["query", "ID", "--limit", "many"],
        ["query", "ID", "--offset", "-1"],
        ["query", "ID", "--topic-id", "bad"],
        ["query", "ID", "--start", "bad"],
        ["query", "ID", "--sentiment", "unknown"],
    ],
)
def test_json_errors_include_usage_failures(analysis, args):
    result = invoke(*(analysis.analysis_id if a == "ID" else a for a in args))
    assert result.exit_code != 0
    value = json.loads(result.stdout)
    assert value["schema_version"] == queries.SCHEMA_VERSION
    assert value["state"] == "error" and value["data"] is None
    assert value["analysis_id"] is None
    assert value["error"]["message"] and result.stderr


def test_corruption_returns_json_error(analysis, tmp_path):
    path = tmp_path / "analyses" / analysis.analysis_id / "analysis.duckdb"
    with path.open("ab") as stream:
        stream.write(b"changed")
    result = invoke("query", analysis.analysis_id)
    assert result.exit_code != 0
    assert "checksum" in json.loads(result.stdout)["error"]["message"]


def test_pretty_output_and_help(analysis):
    result = CliRunner().invoke(main, ["analysis", "query", analysis.analysis_id])
    assert result.exit_code == 0 and json.loads(result.stdout)["state"] == "ok"
    assert '\n  "schema_version"' in result.stdout
    help_result = CliRunner().invoke(main, ["analysis", "query", "--help"])
    assert help_result.exit_code == 0
    assert "--status" in help_result.stdout


def test_no_ml_imports_for_real_cli_process(analysis, tmp_path):
    script = """
import importlib.abc
import sys
class NoML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'voc_ml','torch','transformers','sentence_transformers','bertopic','zenml','mlflow'}:
            raise AssertionError('Unexpected model import: ' + fullname)
sys.meta_path.insert(0, NoML())
from voc.cli import main
main(args=['analysis','query',sys.argv[1],'--json'])
"""
    env = dict(
        os.environ,
        VOC_DATA_DIR=str(tmp_path),
        PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"),
    )
    result = subprocess.run(
        [sys.executable, "-c", script, analysis.analysis_id],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == queries.overview(analysis.analysis_id)
    assert result.stderr == ""
