"""The isolated demo exports verified local files, preserving their identity."""

import json
from uuid import uuid4

import pytest
from click.testing import CliRunner

from tests.unit.test_analysis_local_inputs import local_files as local_files  # noqa: PLC0414
from tests.unit.test_publication_v2 import (  # noqa: F401
    candidate,
    descriptions,
    stored,
)
from voc.cli import main
from voc.publications.builder import build_bundle
from voc.publications.store import validate_bundle


def test_explicit_selection_round_trip(descriptions, tmp_path):  # noqa: F811
    _, description = descriptions
    description["analysis"]["analysis"]["selection_kind"] = "explicit"
    path = build_bundle(description, tmp_path / "bundle")
    assert validate_bundle(path)["schema_version"] == "voc-data-state-v2"


@pytest.mark.parametrize("wrong_identity", [False, True])
def test_local_cli_never_reads_cloud(
    local_files, monkeypatch, tmp_path, wrong_identity
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Cloud reader must not run")

    monkeypatch.setattr("voc.datasets.get_dataset", forbidden)
    dataset = json.loads((local_files.parent / "dataset.json").read_text())
    result = CliRunner().invoke(
        main,
        [
            "publication",
            "build",
            "--source",
            "local",
            "--local-inputs",
            str(local_files),
            "--dataset-version",
            str(uuid4()) if wrong_identity else dataset["release_id"],
            "--output",
            str(tmp_path / "bundle"),
        ],
    )
    if wrong_identity:
        assert result.exit_code != 0
        assert "identity differs" in result.output
        assert not (tmp_path / "bundle").exists()
    else:
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["dataset_id"]
        validate_bundle(tmp_path / "bundle")
