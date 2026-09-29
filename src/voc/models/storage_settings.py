"""Resolved storage configuration and derived paths; no I/O or credential loading."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StorageSettings:
    """Immutable settings returned by voc.storage.load_storage()."""

    artifact_destination: str
    dataset_source: str
    runtime_root: Path
    bucket: str
    region: str
    member_id: str
    shared_prefix: str
    members_prefix: str
    installation_id: str | None

    @property
    def shared_dataset_uri(self):
        return f"s3://{self.bucket}/{self.shared_prefix}" if self.bucket else None

    @property
    def personal_uri(self):
        if self.installation_id is None:
            return None
        return f"s3://{self.bucket}/{self.members_prefix}/{self.member_id}/{self.installation_id}"

    @property
    def zenml_artifact_uri(self):
        if self.artifact_destination == "s3":
            return self.personal_uri + "/zenml-artifacts"
        return str(self.runtime_root / "zenml-artifacts")

    @property
    def mlflow_artifact_uri(self):
        if self.artifact_destination == "s3":
            return self.personal_uri + "/mlflow-artifacts"
        return (self.runtime_root / "mlflow/artifacts").as_uri()

    def describe(self):
        """Non-secret settings for the CLI; these are not active-stack evidence."""
        return {
            "artifact_destination": self.artifact_destination,
            "dataset_source": self.dataset_source,
            "runtime_root": str(self.runtime_root),
            "bucket": self.bucket,
            "region": self.region,
            "member_id": self.member_id,
            "installation_id": self.installation_id,
            "shared_dataset_uri": self.shared_dataset_uri,
            "personal_uri": self.personal_uri,
            "zenml_artifact_uri": self.zenml_artifact_uri,
            "mlflow_artifact_uri": self.mlflow_artifact_uri,
        }
