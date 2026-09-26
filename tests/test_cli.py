from click.testing import CliRunner

from src.mlops.cli import main as ml
from voc.cli import main as voc


def test_separate_cli_commands_and_live_acknowledgement():
    runner = CliRunner()
    assert runner.invoke(voc, ["version"]).output.strip() == "0.1.0"
    assert "dataset-info" in runner.invoke(voc, ["--help"]).output
    assert "dataset" in runner.invoke(ml, ["--help"]).output
    assert runner.invoke(voc, ["dataset", "run"]).exit_code != 0
    result = runner.invoke(ml, ["dataset", "run", "--refresh"])
    assert result.exit_code == 2 and "--live" in result.output
