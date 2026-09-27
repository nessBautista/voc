"""Repeatable offline integration checks against the configured initialized runtime."""

import json
import os
import uuid

from .cli import environment


def main():
    environment()
    from mlflow.tracking import MlflowClient
    from zenml.client import Client

    from voc.collector import get_dataset, get_raw_dataset, load_config
    from voc.datasets import resolve, summary
    from voc.paths import data_root
    from voc.store import RawStore

    from .bootstrap import bootstrap
    from .pipeline import dataset_pipeline
    from .runner import code_identity, publish_run

    bootstrap()
    config = load_config("config/collector.toml")
    raw = RawStore(config["raw_store"])
    original = raw.revision()["id"]

    def run(rules):
        return dataset_pipeline(
            config=config,
            rules=rules,
            refresh=False,
            operation_id=str(uuid.uuid4()),
            project_identity=code_identity(),
        )

    first = run({})
    first_info = publish_run(str(first.id))
    one = get_dataset()
    assert one.info == first_info
    second = run({})
    from unittest.mock import patch

    with patch(
        "src.mlops.runner.publish", side_effect=OSError("simulated catalog unavailable")
    ):
        try:
            publish_run(str(second.id))
        except OSError:
            pass
        else:
            raise AssertionError("Publication failure was not injected")
    assert resolve() == first_info
    second_info = publish_run(str(second.id))
    two = get_dataset()
    assert two.info == second_info
    assert first_info["artifact_id"] != second_info["artifact_id"]
    assert one.data.equals(two.data)
    assert get_dataset(version=first_info["artifact_id"]).data.equals(two.data)
    assert publish_run(str(second.id)) == second_info
    assert resolve() == second_info
    assert len(two.data) == two.info["row_count"]
    assert sum(summary(two)["platform_counts"].values()) == len(two.data)
    # One dataframe output per run, without a full raw dataframe artifact.
    for completed in (first, second):
        current = Client().get_pipeline_run(completed.id)
        assert set(current.steps) == {"raw_revision", "prepare", "track"}
        assert set(current.steps["raw_revision"].outputs) == {"raw_reference"}
        assert set(current.steps["prepare"].outputs) == {
            "workable",
            "preparation_report",
        }
    changed = run({"combine_title_body": True})
    changed_info = publish_run(str(changed.id))
    titled = get_dataset()
    titles = two.data.title.fillna("").str.strip().ne("")
    assert (
        titled.data.loc[titles, "model_text"] != two.data.loc[titles, "model_text"]
    ).any()
    assert (titled.data.text == two.data.text).all()
    assert changed_info["raw_revision_id"] == original
    failed_name = "expected-failure-" + uuid.uuid4().hex
    try:
        dataset_pipeline.with_options(run_name=failed_name)(
            config=config,
            rules={"unknown_rule": True},
            refresh=False,
            operation_id=str(uuid.uuid4()),
            project_identity=code_identity(),
        )
    except Exception:  # noqa: BLE001 -- assertion below checks the specific failed run
        failed = Client().get_pipeline_run(failed_name)
        assert failed.status.value == "failed"
        try:
            publish_run(str(failed.id))
        except RuntimeError:
            pass
        else:
            raise AssertionError("Failed run was published")
    else:
        raise AssertionError("Invalid rules did not fail")
    assert resolve() == changed_info
    assert raw.revision()["id"] == original
    assert (
        get_raw_dataset(config["raw_store"], original).info["revision_id"] == original
    )
    # Return latest to default preparation, while retaining all versions.
    final = run({})
    final_info = publish_run(str(final.id))
    tracker = MlflowClient(
        tracking_uri=(
            os.environ.get("MLFLOW_TRACKING_URI")
            or "sqlite:///" + str(data_root() / "mlflow/mlflow.db")
        )
    )
    tracked = tracker.get_run(final_info["mlflow_run_id"])
    assert tracked.data.metrics["rows"] == final_info["row_count"]
    assert tracked.data.params["workable_artifact_id"] == final_info["artifact_id"]
    assert tracked.data.params["raw_revision_id"] == original
    assert not tracker.list_artifacts(final_info["mlflow_run_id"])
    result = {
        "status": "passed",
        "row_count": len(two.data),
        "raw_revision_id": original,
        "first_artifact": first_info["artifact_id"],
        "second_artifact": second_info["artifact_id"],
        "changed_rules_artifact": changed_info["artifact_id"],
        "failed_run": failed_name,
        "latest_artifact": final_info["artifact_id"],
        "mlflow_run_id": final_info["mlflow_run_id"],
    }
    out = data_root() / "verification.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
