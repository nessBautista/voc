"""Portable representation lineage. Reading never requires the parent directory."""

from voc.embeddings.shared.contracts import fingerprint, require_digest

REPRESENTATION_SCHEMA = "voc-local-topic-representation-v1"


def topic_skeleton(topics):
    return [
        {k: t[k] for k in ("topic_id", "size", "evidence_ids")}
        for t in sorted(topics, key=lambda t: t["topic_id"])
    ]


def validate_representation(manifest, execution, topics):
    parent = execution["parent_manifest"]
    digest = execution["parent_manifest_sha256"]
    require_digest(digest, "parent_manifest_sha256")
    if (
        fingerprint(parent) != digest
        or parent["schema_version"] != "voc-local-topics-v1"
        or parent["state"] != "completed"
    ):
        raise ValueError("Invalid grouping parent manifest")
    if parent["run_id"] == manifest["run_id"]:
        raise ValueError("Representation must have a new identity")
    for name in ("input", "configuration", "counts"):
        if manifest[name] != parent[name]:
            raise ValueError("Representation changed grouping lineage")
    for name in ("selection.parquet", "assignments.parquet", "coordinates.npy"):
        if manifest["files"][name] != parent["files"][name]:
            raise ValueError("Representation changed parent files")
    if fingerprint(topic_skeleton(topics)) != execution["parent_topic_skeleton_sha256"]:
        raise ValueError("Representation changed topic evidence")
    config = execution["representation_config"]
    if config["method"] not in ("ctfidf", "keybert", "keybert_mmr"):
        raise ValueError("Unknown representation method")
    if config != execution["fit"]["configuration"]:
        raise ValueError("Representation configuration mismatch")
