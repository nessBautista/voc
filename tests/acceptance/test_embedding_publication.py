"""Opt-in S3 writes under unique test roots, with cleanup of only those roots.

Requires the configured AWS credentials and a working ZenML/MLflow stack. The
bounded-export check also needs VOC_EMBEDDING_PRODUCER_RUN_ID (<=1,000 reviews).
The full-selection check publishes an actual two-row synthetic dataset and runs
its real producer twice. It never promotes the production dataset pointer.
"""

import json
import os
from dataclasses import replace
from uuid import uuid4

import boto3
import pytest

from tests.acceptance.shared_scenario import sample_pair
from tests.acceptance.test_embeddings import invoke
from voc.datasets.publisher import publish_release as publish_dataset
from voc.embeddings.publisher import publish_release
from voc.embeddings.validation import load_release
from voc.storage import load_storage
from voc.storage.objects import PublicationConflict, ReleaseObjects
from voc.storage.settings import DEFAULTS
from voc_ml.embeddings.export import export_run


def cleanup(client, bucket, prefixes):
    for prefix in prefixes:
        pages = client.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix + "/"
        )
        for page in pages:
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                result = client.delete_objects(
                    Bucket=bucket, Delete={"Objects": objects}
                )
                assert not result.get("Errors"), result.get("Errors")


def verify_download(client, settings, receipt, directory):
    directory.mkdir()
    prefix = receipt["scope_uri"].removeprefix(f"s3://{settings.bucket}/")
    objects = ReleaseObjects(client, settings.bucket, prefix)
    manifest, _ = objects.get_json(f"releases/{receipt['embedding_release_id']}.json")
    paths = {"manifest.json": f"releases/{receipt['embedding_release_id']}.json"}
    paths.update(
        {
            name: manifest["artifacts"][kind]["key"]
            for kind, name in (
                ("vectors", "vectors.npy"),
                ("reviews", "reviews.parquet"),
            )
        }
    )
    for name, key in paths.items():
        client.download_file(settings.bucket, objects.key(key), str(directory / name))
    return load_release(directory, manifest_sha256=receipt["manifest_sha256"])


@pytest.mark.live
def test_bounded_export_publish_and_download(tmp_path):
    run_id = os.environ.get("VOC_EMBEDDING_PRODUCER_RUN_ID")
    if not run_id:
        pytest.skip("Set VOC_EMBEDDING_PRODUCER_RUN_ID to a completed bounded run")
    reference = export_run(run_id)
    assert reference["coverage"]["selected_rows"] <= 1000
    original = load_storage()
    settings = replace(
        original,
        runtime_root=tmp_path / "runtime",
        embeddings_prefix=f"{original.embeddings_prefix}/checks/{uuid4()}",
    )
    client = boto3.client("s3", region_name=settings.region)
    try:
        first = publish_release(reference, settings, client=client)
        assert publish_release(reference, settings, client=client) == first
        bundle = verify_download(client, settings, first, tmp_path / "download")
        assert len(bundle.reviews) == reference["coverage"]["selected_rows"]
        (tmp_path / "publication.json").write_text(json.dumps(first, indent=2))
    finally:
        cleanup(client, settings.bucket, [settings.embeddings_prefix])


@pytest.mark.live
def test_small_full_dataset_promotion_and_stale_retry(tmp_path, monkeypatch):
    original = load_storage()
    check_id = str(uuid4())
    settings = replace(
        original,
        shared_prefix=f"{original.shared_prefix}/checks/{check_id}",
        embeddings_prefix=f"{original.embeddings_prefix}/checks/{check_id}",
    )
    # Keep the installed stack, model cache and tracking configuration. Only the
    # two shared roots change for this test and its child CLI processes.
    config = tmp_path / "storage.toml"
    config.write_text(
        "\n".join(f"{key} = {json.dumps(getattr(settings, key))}" for key in DEFAULTS)
        + "\n"
    )
    monkeypatch.setenv("VOC_STORAGE_CONFIG", str(config))
    monkeypatch.setenv("VOC_EMBEDDINGS_PREFIX", settings.embeddings_prefix)
    client = boto3.client("s3", region_name=settings.region)
    receipts = []
    try:
        raw, workable = sample_pair(check_id)
        dataset = publish_dataset(raw, workable, settings, client=client)
        for index in range(2):
            result, state = invoke(
                [
                    "embeddings",
                    "workflow",
                    "--dataset-version",
                    dataset["release_id"],
                    "--json",
                ]
            )
            assert result.returncode == 0, result.stderr
            assert state["selection"]["kind"] == "full" and state["counts"]["rows"] == 2
            reference = export_run(state["producer_run_id"])
            receipt = publish_release(reference, settings, promote=True, client=client)
            assert (
                publish_release(reference, settings, promote=True, client=client)
                == receipt
            )
            assert (
                len(
                    verify_download(
                        client, settings, receipt, tmp_path / f"download-{index}"
                    ).reviews
                )
                == 2
            )
            receipts.append((reference, receipt))
        with pytest.raises(PublicationConflict):
            publish_release(receipts[0][0], settings, promote=True, client=client)
        prefix = receipts[1][1]["scope_uri"].removeprefix(f"s3://{settings.bucket}/")
        pointer, _ = ReleaseObjects(client, settings.bucket, prefix).latest()
        assert pointer["release_id"] == receipts[1][1]["embedding_release_id"]
        (tmp_path / "full-publications.json").write_text(
            json.dumps([item[1] for item in receipts], indent=2)
        )
    finally:
        cleanup(
            client,
            settings.bucket,
            [settings.shared_prefix, settings.embeddings_prefix],
        )
