"""Read shared data from an empty runtime using only VOC and S3."""

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import boto3
import click

from voc import collector
from voc.storage import load_storage


class ReadCounter:
    def __init__(self, client):
        self.client = client
        self.keys = []
        self.offline = False

    def get_object(self, **kwargs):
        if self.offline:
            raise OSError("Diagnostic: network disabled")
        self.keys.append(kwargs["Key"])
        return self.client.get_object(**kwargs)

    @property
    def parquet_downloads(self):
        return sum(key.endswith(".parquet") for key in self.keys)


@click.command()
@click.option("--version", default="latest", help="Shared release UUID or latest.")
def main(version):
    settings = load_storage(destination="local")
    counter = ReadCounter(boto3.client("s3", region_name=settings.region))
    with (
        TemporaryDirectory(prefix="voc-shared-reader-") as temporary,
        patch.dict(os.environ, {"VOC_DATA_DIR": temporary}),
        patch("voc.shared_reader.boto3.client", return_value=counter),
    ):
        prepared = collector.get_dataset(source="s3", version=version)
        release = prepared.info["release_id"]
        raw = collector.get_dataset(source="s3", stage="raw", version=release)
        downloads = counter.parquet_downloads
        # Disable all further S3 calls. Pinned reads must use verified cache.
        counter.offline = True
        pinned = collector.get_dataset(source="s3", version=release)
        pinned_raw = collector.get_dataset(source="s3", stage="raw", version=release)
        assert prepared.data.equals(pinned.data)
        assert raw.data.equals(pinned_raw.data)
        assert raw.info["raw_revision_id"] == prepared.info["raw_revision_id"]
        assert downloads == 2 and counter.parquet_downloads == downloads
        assert sorted(p.name for p in Path(temporary).iterdir()) == ["cache"]
        assert "zenml" not in sys.modules and "mlflow" not in sys.modules
        report = {
            "status": "passed",
            "release_id": release,
            "raw_revision_id": raw.info["raw_revision_id"],
            "artifact_id": prepared.info["artifact_id"],
            "raw_row_count": len(raw.data),
            "workable_row_count": len(prepared.data),
            "parquet_downloads": downloads,
            "cached_reread_downloads": 0,
            "pinned_offline_read": True,
            "publisher_metadata_required": False,
        }
    target = settings.runtime_root / "checks/shared-reader.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    click.echo(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
