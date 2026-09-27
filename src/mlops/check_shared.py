"""Exercise shared-release publication under a unique personal test prefix."""

import hashlib
import json
import uuid
from dataclasses import replace

import boto3
import click
import pandas as pd

from src.preparation.dataset import COLUMNS, prepare_dataset
from voc.shared import (
    PublicationConflict,
    ReleaseObjects,
    canonical_id,
    frame_hash,
    json_bytes,
    publish_release,
    validate_manifest,
)
from voc.storage import load_storage
from voc.store import Snapshot


def sample_pair(check_id, *, titles=False):
    def identity(label):
        return str(uuid.uuid5(uuid.UUID(check_id), label))

    rows = []
    for i in range(2):
        row = {
            column: None
            for column in COLUMNS
            if column not in ("model_text", "text_hash")
        }
        row.update(
            record_id=f"check-{i}",
            platform="google_play",
            app_id="example.app",
            country="MX",
            text="Synthetic review",
            title="Example title",
            rating=4,
            record_date="2026-09-01",
            date_basis="provider_published_at",
            date_precision="day",
            date_timezone="unknown",
            language_basis="not detected",
            relevance="app_target_match",
            preferred_provider="play",
            identity_basis="provider_identity",
        )
        rows.append(row)
    raw = Snapshot(
        pd.DataFrame(rows),
        {"revision_id": identity("raw"), "raw_store_id": "synthetic-check"},
    )
    prepared = prepare_dataset(raw.data, {"combine_title_body": titles})
    info = {
        "run_id": identity("run-" + str(titles)),
        "artifact_id": identity("artifact-" + str(titles)),
        "raw_revision_id": raw.info["revision_id"],
        "raw_store_id": raw.info["raw_store_id"],
        "row_count": len(prepared.data),
        "schema_version": "workable-v1",
        "content_sha256": frame_hash(prepared.data),
        "rules": prepared.report["rules"],
        "project_identity": "synthetic-storage-check",
        "mlflow_run_id": None,
    }
    return raw, Snapshot(prepared.data, info)


def verify_saved(report, objects):
    pointer, _ = objects.latest()
    if pointer["release_id"] != report["second_release"]["release_id"]:
        raise ValueError("Diagnostic latest no longer names the second release")
    raw_keys = []
    for entry in (report["first_release"], report["second_release"]):
        manifest, _ = objects.get_json(
            f"releases/{canonical_id(entry['release_id'])}.json"
        )
        if hashlib.sha256(json_bytes(manifest)).hexdigest() != entry["manifest_sha256"]:
            raise ValueError("Diagnostic manifest checksum differs")
        validate_manifest(manifest, objects)
        for stage in ("raw", "workable"):
            item = manifest[stage]
            relative = item["key"][len(objects.prefix) + 1 :]
            if not objects.verify_file(relative, item["sha256"], item["size_bytes"]):
                raise ValueError("Missing diagnostic dataset")
        raw_keys.append(manifest["raw"]["key"])
    if raw_keys[0] != raw_keys[1]:
        raise ValueError("Diagnostic releases did not reuse the raw object")


@click.command()
@click.option(
    "--reload",
    "reload_existing",
    is_flag=True,
    help="Verify the saved pair without creating another release.",
)
def main(reload_existing):
    settings = load_storage(destination="s3")
    report_path = settings.runtime_root / "checks/shared-publication.json"
    if reload_existing:
        if not report_path.exists():
            raise click.ClickException("No saved check. Run without --reload first.")
        report = json.loads(report_path.read_text())
        check_id = canonical_id(report["check_id"])
    else:
        check_id = str(uuid.uuid4())
    # Only this diagnostic overrides the dataset root; production config is untouched.
    settings = replace(
        settings,
        shared_prefix=(
            f"{settings.members_prefix}/{settings.member_id}/{settings.installation_id}/tests/shared/{check_id}"
        ),
    )
    client = boto3.client("s3", region_name=settings.region)
    objects = ReleaseObjects(client, settings.bucket, settings.shared_prefix)
    if not reload_existing:
        raw, first = sample_pair(check_id)
        _, second = sample_pair(check_id, titles=True)
        first_release = publish_release(raw, first, settings, client=client)
        if publish_release(raw, first, settings, client=client) != first_release:
            raise ValueError("Repeated publication was not idempotent")
        second_release = publish_release(raw, second, settings, client=client)
        try:
            publish_release(raw, first, settings, client=client)
        except PublicationConflict:
            pass
        else:
            raise ValueError("Old release unexpectedly replaced latest")
        report = {
            "status": "passed",
            "check_id": check_id,
            "dataset_uri": settings.shared_dataset_uri,
            "first_release": first_release,
            "second_release": second_release,
            "raw_reused": True,
            "stale_retry_rejected": True,
        }
    if report["dataset_uri"] != settings.shared_dataset_uri:
        raise ValueError("Saved check belongs to another storage configuration")
    verify_saved(report, objects)
    if not reload_existing:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    click.echo(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
