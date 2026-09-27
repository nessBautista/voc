"""Registration guard checks without modifying a ZenML server or AWS."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.mlops.bootstrap import ensure_component, zenml_store_settings
from voc.storage import StorageSettings


def storage(tmp_path, destination):
    return StorageSettings(
        artifact_destination=destination,
        dataset_source="local",
        runtime_root=tmp_path,
        bucket="team-test-bucket",
        region="us-east-2",
        member_id="ness",
        shared_prefix="voc/datasets",
        members_prefix="members",
        installation_id="9cfc0776-d12f-4175-98ec-bd90ca3eb2b6"
        if destination == "s3"
        else None,
    )


def test_local_registration_names_and_path_are_unchanged(tmp_path):
    stack, name, flavor, config = zenml_store_settings(storage(tmp_path, "local"))
    assert (stack, name, flavor) == ("voc-local", "voc-artifacts", "local")
    assert config == {"path": str(tmp_path / "zenml-artifacts")}


def test_s3_registration_is_personal_and_never_embeds_environment_secrets(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "synthetic-access-id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret-value")
    settings = storage(tmp_path, "s3")
    stack, name, flavor, config = zenml_store_settings(settings)
    assert stack == "voc-s3-" + settings.installation_id
    assert name == "voc-artifacts-s3-" + settings.installation_id
    assert flavor == "s3"
    assert config == {
        "path": settings.zenml_artifact_uri,
        "client_kwargs": {"region_name": "us-east-2"},
    }
    assert "synthetic" not in json.dumps(config)


def test_missing_component_is_created_but_matching_one_is_reused():
    client = Mock()
    config = {"path": "/data/zenml-artifacts"}
    client.get_stack_component.side_effect = KeyError("missing")
    ensure_component(client, "artifact_store", "voc-artifacts", "local", config)
    client.create_stack_component.assert_called_once_with(
        "voc-artifacts", "local", "artifact_store", config
    )
    client.reset_mock()
    client.get_stack_component.side_effect = None
    item = SimpleNamespace(flavor_name="local", configuration=config)
    client.get_stack_component.return_value = item
    assert (
        ensure_component(client, "artifact_store", "voc-artifacts", "local", config)
        is item
    )
    client.create_stack_component.assert_not_called()
    client.update_stack_component.assert_not_called()


@pytest.mark.parametrize(
    "flavor,actual",
    [
        ("local", {"path": "s3://team/a"}),
        ("s3", {"path": "s3://team/elsewhere"}),
        ("s3", {"path": "s3://team/a", "secret": "synthetic-secret"}),
    ],
)
def test_conflicting_component_is_rejected_without_mutation(flavor, actual):
    client = Mock()
    client.get_stack_component.return_value = SimpleNamespace(
        flavor_name=flavor, configuration=actual
    )
    with pytest.raises(ValueError):
        ensure_component(
            client, "artifact_store", "personal", "s3", {"path": "s3://team/a"}
        )
    client.create_stack_component.assert_not_called()
    client.update_stack_component.assert_not_called()
