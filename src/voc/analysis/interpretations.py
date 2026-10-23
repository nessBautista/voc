"""Provider-independent validation and reading of saved topic interpretations."""

import json
from datetime import datetime

from voc.embeddings.contracts import (
    canonical_json,
    fingerprint,
    require_digest,
    require_key,
    require_uuid,
)

SCHEMA = "voc-topic-interpretation-v1"
VALIDATION_VERSION = "topic-interpretation-v1"
RESULT_FIELDS = {
    "name",
    "topic_description",
    "proposed_feature",
    "assessment",
    "supporting_review_ids",
}


def evidence_labels(evidence_ids):
    """Short labels the model cites instead of copying long review IDs: E1, E2, …"""
    return {f"E{i + 1}": rid for i, rid in enumerate(evidence_ids)}


def normalize_result(result, evidence_ids):
    """Validate a raw model result, mapping cited labels back to review IDs.

    Prompt v3 responses cite ``supporting_evidence`` labels; earlier responses
    carried ``supporting_review_ids`` and pass through unchanged. The stored
    result always holds review IDs, so consumers never see labels.
    """
    if (
        isinstance(result, dict)
        and "supporting_evidence" in result
        and "supporting_review_ids" not in result
    ):
        labels = evidence_labels(evidence_ids)
        cited = result["supporting_evidence"]
        if (
            not isinstance(cited, list)
            or not cited
            or not all(isinstance(c, str) and c in labels for c in cited)
            or len(set(cited)) != len(cited)
        ):
            raise ValueError("Supporting evidence must be unique supplied labels")
        result = {k: v for k, v in result.items() if k != "supporting_evidence"}
        result["supporting_review_ids"] = [labels[c] for c in cited]
    return validate_result(result, evidence_ids)


def validate_result(result, evidence_ids):
    if not isinstance(result, dict) or set(result) != RESULT_FIELDS:
        raise ValueError("Interpretation response fields differ from the contract")
    for key, maximum in (("name", 160), ("topic_description", 2000)):
        if (
            not isinstance(result[key], str)
            or not result[key].strip()
            or len(result[key]) > maximum
        ):
            raise ValueError("Invalid interpretation wording")
    assessment = result["assessment"]
    if assessment not in ("single_feature", "mixed", "unclear"):
        raise ValueError("Invalid interpretation assessment")
    feature = result["proposed_feature"]
    if assessment == "single_feature":
        if not isinstance(feature, str) or not feature.strip() or len(feature) > 300:
            raise ValueError("A single feature requires a supported feature name")
    elif feature is not None:
        raise ValueError("Mixed/unclear topics must not propose one feature")
    ids = result["supporting_review_ids"]
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(i, str) for i in ids)
        or len(set(ids)) != len(ids)
        or not set(ids) <= set(evidence_ids)
    ):
        raise ValueError("Supporting IDs must be unique supplied evidence IDs")
    canonical_json(result)
    return result


def validate_records(records, assignments):
    if not isinstance(records, (list, tuple)):
        raise TypeError("Interpretations must be a sequence")
    members = {}
    for row in assignments:
        members.setdefault(row.topic_id, set()).add(row.record_id)
    seen = set()
    fields = {
        "schema_version",
        "topic_id",
        "status",
        "result",
        "error",
        "request_fingerprint",
        "prompt_version",
        "validation_version",
        "evidence_ids",
        "request_artifact",
        "response_artifact",
        "attempt_id",
        "created_at",
        "provider",
        "reuse_origin",
    }
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != fields
            or record["schema_version"] != SCHEMA
        ):
            raise ValueError("Unsupported interpretation record")
        tid = record["topic_id"]
        if type(tid) is not int or tid < 0 or tid not in members or tid in seen:
            raise ValueError("Interpretation must target one distinct assigned topic")
        seen.add(tid)
        ids = record["evidence_ids"]
        if (
            not isinstance(ids, list)
            or not ids
            or not all(isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids))
            or not set(ids) <= members[tid]
        ):
            raise ValueError("Interpretation evidence must belong to its topic")
        require_digest(record["request_fingerprint"], "request_fingerprint")
        require_uuid(record["attempt_id"], "attempt_id")
        if datetime.fromisoformat(record["created_at"]).utcoffset() is None:
            raise ValueError("Interpretation timestamp requires timezone")
        if (
            not isinstance(record["prompt_version"], str)
            or not record["prompt_version"]
        ):
            raise ValueError("Interpretation requires prompt version")
        if record["validation_version"] != VALIDATION_VERSION:
            raise ValueError("Unsupported interpretation validation version")
        for name in ("request_artifact", "response_artifact"):
            require_key(record[name])
            if not record[name].startswith("artifacts/interpretation/"):
                raise ValueError("Interpretation artifacts must live inside its bundle")
        if not isinstance(record["provider"], dict):
            raise TypeError("Interpretation requires provider metadata")
        provider = record["provider"]
        if set(provider) != {
            "requested_model",
            "returned_model",
            "request_id",
            "usage",
        }:
            raise ValueError("Invalid interpretation provider metadata")
        if (
            not isinstance(provider["requested_model"], str)
            or not provider["requested_model"]
        ):
            raise ValueError("Interpretation requires requested model")
        if record["status"] == "completed" and any(
            not isinstance(provider[k], str) or not provider[k]
            for k in ("returned_model", "request_id")
        ):
            raise ValueError(
                "Successful interpretation requires returned model and request ID"
            )
        origin = record["reuse_origin"]
        if origin is not None:
            if not isinstance(origin, dict) or set(origin) != {
                "analysis_id",
                "attempt_id",
                "response_sha256",
            }:
                raise ValueError("Invalid interpretation reuse origin")
            require_uuid(origin["analysis_id"], "reuse analysis_id")
            require_uuid(origin["attempt_id"], "reuse attempt_id")
            require_digest(origin["response_sha256"], "reuse response_sha256")
        if record["status"] == "completed":
            if record["error"] is not None:
                raise ValueError("Successful interpretation cannot contain an error")
            validate_result(record["result"], ids)
        elif record["status"] == "failed":
            if record["result"] is not None or record["error"] not in (
                "provider_error",
                "invalid_response",
            ):
                raise ValueError("Invalid failed interpretation")
        else:
            raise ValueError("Unsupported interpretation status")
        canonical_json(record)


def read_records(con, manifest):
    """Old candidates remain readable without a new table or schema migration."""
    if manifest.schema_version == "voc-analysis-v1":
        return ()
    rows = con.execute(
        "SELECT topic_id, payload FROM interpretations ORDER BY topic_id"
    ).fetchall()
    result = []
    for topic_id, payload in rows:
        record = json.loads(payload)
        if record.get("topic_id") != topic_id:
            raise ValueError("Interpretation table ID mismatch")
        result.append(record)
    return tuple(result)


def verify_artifacts(records, manifest, directory):
    """Cross-check request identity and normalized output against portable artifacts."""
    for record in records:
        for key in ("request_artifact", "response_artifact"):
            if record[key] not in manifest.artifacts:
                raise ValueError("Undeclared interpretation artifact")
        request = json.loads((directory / record["request_artifact"]).read_bytes())
        if fingerprint(request) != record["request_fingerprint"]:
            raise ValueError("Interpretation request fingerprint mismatch")
        if (
            request["prompt_version"] != record["prompt_version"]
            or request["validation_version"] != record["validation_version"]
        ):
            raise ValueError("Interpretation version mismatch")
        evidence = json.loads(request["body"]["messages"][1]["content"])
        if (
            evidence["topic_id"] != record["topic_id"]
            or [r["record_id"] for r in evidence["evidence"]] != record["evidence_ids"]
        ):
            raise ValueError("Interpretation request evidence mismatch")
        response = json.loads((directory / record["response_artifact"]).read_bytes())
        expected_provider = {
            "requested_model": request["body"]["model"],
            "returned_model": response.get("model"),
            "request_id": response.get("id"),
            "usage": response.get("usage"),
        }
        if record["provider"] != expected_provider:
            raise ValueError(
                "Interpretation provider metadata differs from saved artifacts"
            )
        if record["status"] == "completed":
            if response.get("error") is not None:
                raise ValueError("Successful interpretation has failed raw response")
            choice = response["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("refusal"):
                raise ValueError("Interpretation response incomplete or refused")
            result = normalize_result(
                json.loads(choice["message"]["content"]), record["evidence_ids"]
            )
            if result != record["result"]:
                raise ValueError("Interpretation differs from raw response")
