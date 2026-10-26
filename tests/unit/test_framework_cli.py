import json

from click.testing import CliRunner

from voc.cli import main


def test_unpublished_dataset_returns_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    result = CliRunner().invoke(main, ["dataset-info", "--source", "personal"])
    assert result.exit_code == 1


def test_metadata_command_selects_pinned_artifact_instead_of_latest(tmp_path, monkeypatch):
    from voc.datasets import publish

    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path))
    older = {
        "run_id": "run-older",
        "artifact_id": "artifact-older",
        "raw_revision_id": "raw-older",
        "schema_version": "workable-v1",
        "row_count": 2,
    }
    newer = older | {
        "run_id": "run-newer",
        "artifact_id": "artifact-newer",
        "raw_revision_id": "raw-newer",
        "row_count": 3,
    }
    publish(older)
    publish(newer)
    runner = CliRunner()
    pinned = runner.invoke(
        main,
        ["dataset-info", "--source", "personal", "--version", older["artifact_id"]],
    )
    latest = runner.invoke(main, ["dataset-info", "--source", "personal"])
    assert pinned.exit_code == latest.exit_code == 0
    assert json.loads(pinned.output) == older
    assert json.loads(latest.output) == newer
