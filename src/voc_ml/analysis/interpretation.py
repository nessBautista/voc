"""Plan or execute bounded interpretation, reusing verified saved candidates."""

import json
import platform
import tempfile
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from voc.analysis.interpretations import SCHEMA, VALIDATION_VERSION, validate_result
from voc.analysis.store import list_analyses, read_analysis, write_analysis
from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import canonical_json, fingerprint, require_uuid
from voc.paths import data_root
from voc_ml.identity import code_identity
from voc_ml.llm import openrouter
from voc_ml.llm.topic_interpretation import (
    PROMPT_VERSION,
    load_config,
    make_requests,
    normalize,
)

from .representation import preserved_content


def find_reusable(requests):
    """Newest verified candidate wins. Corruption fails closed, never triggers calls."""
    wanted = {r["request_fingerprint"]: r for r in requests}
    found = {}
    for manifest in list_analyses():
        if manifest.schema_version == "voc-analysis-v1":
            continue
        saved = read_analysis(manifest.analysis_id)
        for record in saved.interpretations:
            key = record["request_fingerprint"]
            if key not in wanted or key in found or record["status"] != "completed":
                continue
            request = wanted[key]
            if record["validation_version"] != VALIDATION_VERSION:
                continue
            validate_result(record["result"], request["evidence_ids"])
            root = data_root() / "analyses" / manifest.analysis_id
            # A hash match is also checked against the complete canonical request.
            if (
                json.loads((root / record["request_artifact"]).read_bytes())
                != request["request"]
            ):
                raise ValueError(
                    "Saved interpretation request differs despite matching fingerprint"
                )
            found[key] = {
                "record": record,
                "analysis_id": manifest.analysis_id,
                "response_sha256": manifest.artifacts[record["response_artifact"]][
                    "sha256"
                ],
                "request_bytes": (root / record["request_artifact"]).read_bytes(),
                "response_bytes": (root / record["response_artifact"]).read_bytes(),
            }
        if len(found) == len(wanted):
            break
    return found


def _start_tracking(run_id, parent_id, child_id):
    from mlflow.tracking import MlflowClient

    from voc.storage import load_storage
    from voc_ml.tracking import ensure_analysis_experiment, tracking_uri

    storage = load_storage()
    client = MlflowClient(tracking_uri=tracking_uri(storage))
    experiment = ensure_analysis_experiment(client, storage)
    run = client.create_run(
        experiment,
        tags={
            "mlflow.runName": "interpretation-" + run_id,
            "workflow_kind": "interpretation",
            "producer_run_id": run_id,
            "parent_analysis_id": parent_id,
            "analysis_id": child_id,
        },
    )
    return client, run.info.run_id


def _finish_tracking(client, tracking_id, report):
    client.log_dict(tracking_id, report, "interpretation-evidence.json")
    for key, value in report["counts"].items():
        client.log_metric(tracking_id, key, value)
    client.log_param(
        tracking_id, "model", report["configuration"]["interpretation"]["model"]
    )
    with tempfile.TemporaryDirectory() as directory:
        path = client.download_artifacts(
            tracking_id, "interpretation-evidence.json", directory
        )
        if fingerprint(json.loads(Path(path).read_bytes())) != fingerprint(report):
            raise ValueError("Interpretation tracking round-trip differs")
    client.set_terminated(tracking_id, status="FINISHED")
    if client.get_run(tracking_id).info.status != "FINISHED":
        raise ValueError("Interpretation tracking did not finish")


def interpret(parent_id, topic_ids, config_path, *, live=False, refresh=False):
    require_uuid(parent_id, "parent_analysis_id")
    config = load_config(config_path)
    parent = read_analysis(parent_id)
    requests = make_requests(parent, topic_ids, config)
    reusable = {} if refresh else find_reusable(requests)
    plan = {
        "schema_version": "voc-interpretation-producer-v1",
        "state": "dry_run",
        "parent_analysis_id": parent_id,
        "configuration": asdict(config),
        "refresh": refresh,
        "prompt_version": PROMPT_VERSION,
        "validation_version": VALIDATION_VERSION,
        "maximum_requests": len(requests) - len(reusable),
        "reusable_topics": len(reusable),
        "sdk_retries": 0,
        "requests": [
            {
                "topic_id": r["topic_id"],
                "request_fingerprint": r["request_fingerprint"],
                "evidence_ids": r["evidence_ids"],
                "evidence_count": len(r["evidence_ids"]),
                "reused_from": reusable.get(r["request_fingerprint"], {}).get(
                    "analysis_id"
                ),
                "request": r["request"],
            }
            for r in requests
        ],
    }
    if not live:
        return plan

    run_id, child_id = str(uuid4()), str(uuid4())
    directory = data_root() / "interpretation-runs" / run_id
    directory.mkdir(parents=True, exist_ok=False)
    state = {k: v for k, v in plan.items() if k != "requests"}
    state.update(
        state="running",
        run_id=run_id,
        candidate_id=child_id,
        directory=str(directory),
        tracking_verified=False,
    )
    (directory / "plan.json").write_bytes(canonical_json(plan))

    def save():
        state["updated_at"] = datetime.now(UTC).isoformat()
        temporary = directory / "state.tmp"
        temporary.write_bytes(canonical_json(state))
        temporary.replace(directory / "state.json")

    save()
    transport = tracker = tracking_id = None
    try:
        # Fully reused runs never need a key or a provider client.
        if plan["maximum_requests"]:
            transport = openrouter.create_client()
        tracker, tracking_id = _start_tracking(run_id, parent_id, child_id)
        state["mlflow_run_id"] = tracking_id
        save()
        records = {r["topic_id"]: deepcopy(r) for r in parent.interpretations}
        reports = {}
        root = data_root() / "analyses" / parent_id
        for record in records.values():
            for key in ("request_artifact", "response_artifact"):
                reports[record[key].removeprefix("artifacts/")] = (
                    root / record[key]
                ).read_bytes()
        counts = {
            "selected_topics": len(requests),
            "reused_topics": 0,
            "provider_attempts": 0,
            "successful_topics": 0,
            "failed_topics": 0,
        }
        outcomes = []
        for selected in requests:
            key, tid = selected["request_fingerprint"], selected["topic_id"]
            match = reusable.get(key)
            if match:
                record = deepcopy(match["record"])
                record["reuse_origin"] = {
                    "analysis_id": match["analysis_id"],
                    "attempt_id": record["attempt_id"],
                    "response_sha256": match["response_sha256"],
                }
                reports[record["request_artifact"].removeprefix("artifacts/")] = match[
                    "request_bytes"
                ]
                reports[record["response_artifact"].removeprefix("artifacts/")] = match[
                    "response_bytes"
                ]
                counts["reused_topics"] += 1
            else:
                attempt_id = str(uuid4())
                prefix = "interpretation/" + attempt_id
                request_bytes = canonical_json(selected["request"])
                reports[prefix + "-request.json"] = request_bytes
                (directory / (attempt_id + "-request.json")).write_bytes(request_bytes)
                counts["provider_attempts"] += 1
                state["counts"] = counts.copy()
                save()
                response = openrouter.request(transport, selected["request"]["body"])
                response_bytes = canonical_json(response)
                reports[prefix + "-response.json"] = response_bytes
                (directory / (attempt_id + "-response.json")).write_bytes(
                    response_bytes
                )
                result, error = None, None
                try:
                    result = normalize(response, selected["evidence_ids"])
                except (ValueError, TypeError, KeyError, IndexError):
                    error = (
                        "provider_error"
                        if response.get("error")
                        else "invalid_response"
                    )
                record = {
                    "schema_version": SCHEMA,
                    "topic_id": tid,
                    "status": "completed" if error is None else "failed",
                    "result": result,
                    "error": error,
                    "request_fingerprint": key,
                    "prompt_version": PROMPT_VERSION,
                    "validation_version": VALIDATION_VERSION,
                    "evidence_ids": selected["evidence_ids"],
                    "request_artifact": "artifacts/" + prefix + "-request.json",
                    "response_artifact": "artifacts/" + prefix + "-response.json",
                    "attempt_id": attempt_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "provider": {
                        "requested_model": config.model,
                        "returned_model": response.get("model"),
                        "request_id": response.get("id"),
                        "usage": response.get("usage"),
                    },
                    "reuse_origin": None,
                }
            records[tid] = record
            counts[
                "successful_topics"
                if record["status"] == "completed"
                else "failed_topics"
            ] += 1
            outcomes.append(
                {
                    "topic_id": tid,
                    "status": record["status"],
                    "error": record["error"],
                    "reused": match is not None,
                    "attempt_id": record["attempt_id"],
                    "new_usage": {"total_tokens": 0}
                    if match
                    else record["provider"]["usage"],
                }
            )
            state.update(counts=counts.copy(), outcomes=outcomes.copy())
            save()
        configuration = parent.manifest.configuration | {
            "interpretation": asdict(config)
        }
        preserved = preserved_content(parent)
        software = {
            "python": platform.python_version(),
            "duckdb": version("duckdb"),
            "openai": version("openai"),
            "mlflow": version("mlflow"),
        }
        code = code_identity()
        report = {
            "schema_version": "voc-interpretation-evidence-v1",
            "run_id": run_id,
            "analysis_id": child_id,
            "parent_analysis_id": parent_id,
            "parent_manifest_sha256": file_hash(root / "manifest.json"),
            "configuration": configuration,
            "coverage": parent.manifest.coverage,
            "counts": counts,
            "outcomes": outcomes,
            "refresh": refresh,
            "preserved_content": {"parent": preserved, "child": preserved},
            "software": software,
            "code_sha256": code,
        }
        (directory / "evidence.json").write_bytes(canonical_json(report))
        _finish_tracking(tracker, tracking_id, report)
        state["tracking_verified"] = True
        reports["interpretation-evidence.json"] = canonical_json(report)
        child = write_analysis(
            parent.reviews,
            parent.assignments,
            parent.topics,
            parent.sentiments,
            inputs=parent.manifest.inputs,
            configuration=configuration,
            code_sha256=code,
            software=software,
            provenance={
                "run_id": run_id,
                "mlflow_run_id": tracking_id,
                "kind": "interpretation",
            },
            analysis_id=child_id,
            parent_analysis_id=parent_id,
            reports=reports,
            interpretations=tuple(records[t] for t in sorted(records)),
        )
        state.update(
            state="partial" if counts["failed_topics"] else "completed",
            analysis_id=child.analysis_id,
        )
        if counts["failed_topics"]:
            state["error"] = {
                "message": "Candidate saved with failed topic interpretations. Inspect outcomes before retrying."
            }
        save()
    except (Exception, KeyboardInterrupt, SystemExit) as error:  # noqa: BLE001 - persist sanitized failure without SDK secrets
        if tracker is not None and tracking_id is not None:
            try:
                tracker.set_terminated(tracking_id, status="FAILED")
            except Exception:  # noqa: BLE001 - preserve original failure without exposing SDK messages
                state["tracking_cleanup_failed"] = True
        state.update(
            state="failed",
            error={
                "type": type(error).__name__,
                "message": "Interpretation workflow failed. Check credentials, tracking and saved local attempt artifacts.",
            },
        )
        save()
    finally:
        if transport is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001 - cleanup must not expose transport internals
                state["transport_cleanup_failed"] = True
                save()
    return state
