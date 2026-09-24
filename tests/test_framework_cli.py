from click.testing import CliRunner

from voc.cli import main


def test_framework_help_and_unpublished_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    runner = CliRunner()
    assert runner.invoke(main, ["version"]).output.strip() == "0.1.0"
    assert "dataset-info" in runner.invoke(main, ["--help"]).output
    result = runner.invoke(main, ["dataset-info"])
    assert result.exit_code != 0
    assert "No published dataset" in result.output


def test_metadata_command_delegates_version(monkeypatch):
    import voc.datasets
    seen = []

    def resolve(version):
        seen.append(version)
        return {"artifact_id": version, "row_count": 10}

    monkeypatch.setattr(voc.datasets, "resolve", resolve)
    result = CliRunner().invoke(main, ["dataset-info", "--version", "fixture-id"])
    assert result.exit_code == 0
    assert seen == ["fixture-id"]
    assert '"row_count": 10' in result.output
