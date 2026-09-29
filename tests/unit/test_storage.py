"""Storage identity and namespace guards, with no AWS or tool registration."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from click.testing import CliRunner

from voc.cli import main
from voc.storage.settings import installation_id, load_storage


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    for name in (
        "AWS_BUCKET",
        "AWS_REGION",
        "VOC_MEMBER_ID",
        "VOC_STORAGE_CONFIG",
        "VOC_ARTIFACT_DESTINATION",
        "VOC_DATASET_SOURCE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def s3_env(monkeypatch):
    monkeypatch.setenv("AWS_BUCKET", "team-test-bucket")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("VOC_MEMBER_ID", "ness")


def test_local_defaults_keep_existing_paths_without_creating_files(tmp_path):
    settings = load_storage()
    assert settings.artifact_destination == "local"
    assert settings.dataset_source == "personal"
    assert settings.installation_id is None
    assert settings.zenml_artifact_uri == str(tmp_path / "runtime/zenml-artifacts")
    assert (
        settings.mlflow_artifact_uri == (tmp_path / "runtime/mlflow/artifacts").as_uri()
    )
    assert not (tmp_path / "runtime").exists()


def test_s3_identity_is_reused_and_new_runtime_has_new_identity(
    tmp_path, s3_env, monkeypatch
):
    first = load_storage(destination="s3")
    assert load_storage(destination="s3") == first
    assert (
        first.personal_uri
        == f"s3://team-test-bucket/members/ness/{first.installation_id}"
    )
    assert first.zenml_artifact_uri == first.personal_uri + "/zenml-artifacts"
    assert first.mlflow_artifact_uri == first.personal_uri + "/mlflow-artifacts"
    assert first.shared_dataset_uri == "s3://team-test-bucket/voc/datasets"
    assert load_storage(destination="local").installation_id is None
    assert load_storage(destination="s3").installation_id == first.installation_id
    monkeypatch.setenv("VOC_DATA_DIR", str(tmp_path / "other-runtime"))
    assert load_storage(destination="s3").installation_id != first.installation_id


def test_concurrent_initialization_chooses_one_complete_id(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: installation_id(tmp_path), range(24)))
    assert len(set(results)) == 1
    assert (tmp_path / "storage/installation-id").read_text().strip() == results[0]
    assert len(list((tmp_path / "storage").iterdir())) == 1


def test_corrupt_identity_fails_without_replacing_it(tmp_path):
    folder = tmp_path / "storage"
    folder.mkdir()
    path = folder / "installation-id"
    path.write_text("broken")
    with pytest.raises(ValueError, match="restore it"):
        installation_id(tmp_path)
    assert path.read_text() == "broken"


@pytest.mark.parametrize(
    "shared,members",
    [
        ("voc/datasets", "voc"),
        ("voc", "voc/members"),
        ("members", "members"),
        ("../datasets", "members"),
        ("voc//datasets", "members"),
        ("s3://bucket/datasets", "members"),
    ],
)
def test_bad_or_overlapping_prefixes_rejected_before_creating_identity(
    tmp_path, shared, members, s3_env
):
    path = tmp_path / "settings.toml"
    path.write_text(f'shared_prefix = "{shared}"\nmembers_prefix = "{members}"\n')
    with pytest.raises(ValueError):
        load_storage(path, destination="s3")
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize(
    "name,value",
    [
        ("AWS_BUCKET", "s3://team-test-bucket"),
        ("AWS_REGION", "Ohio us-east-2"),
        ("VOC_MEMBER_ID", "../other-member"),
    ],
)
def test_invalid_s3_settings_fail_early(tmp_path, s3_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        load_storage(destination="s3")
    assert not (tmp_path / "runtime").exists()


def test_environment_overrides_file_and_cli_overrides_destination(
    tmp_path, s3_env, monkeypatch
):
    path = tmp_path / "settings.toml"
    path.write_text('bucket = "fallback-bucket"\nartifact_destination = "local"\n')
    monkeypatch.setenv("VOC_ARTIFACT_DESTINATION", "s3")
    assert load_storage(path).bucket == "team-test-bucket"
    assert load_storage(path).artifact_destination == "s3"
    assert load_storage(path, destination="local").artifact_destination == "local"


def test_shared_reads_do_not_require_personal_identity(tmp_path, s3_env, monkeypatch):
    monkeypatch.delenv("VOC_MEMBER_ID")
    monkeypatch.setenv("VOC_DATASET_SOURCE", "s3")
    settings = load_storage()
    assert settings.dataset_source == "s3"
    assert settings.installation_id is None
    assert not (tmp_path / "runtime").exists()


def test_explicit_missing_file_and_unknown_settings_are_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_storage(tmp_path / "missing.toml")
    path = tmp_path / "settings.toml"
    path.write_text('artifact_destinaton = "s3"\n')
    with pytest.raises(ValueError, match="Unknown storage setting"):
        load_storage(path)


def test_cli_reports_paths_and_explains_missing_settings(s3_env, monkeypatch):
    result = CliRunner().invoke(main, ["storage-info", "--destination", "s3"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["personal_uri"].startswith(
        "s3://team-test-bucket/members/ness/"
    )
    monkeypatch.delenv("AWS_BUCKET")
    result = CliRunner().invoke(main, ["storage-info", "--destination", "s3"])
    assert result.exit_code == 1
    assert "Set AWS_BUCKET" in result.output


@pytest.mark.parametrize("source", ["personal", "local"])
def test_personal_source_and_legacy_config(source, monkeypatch):
    monkeypatch.setenv("VOC_DATASET_SOURCE", source)
    assert load_storage().dataset_source == "personal"
