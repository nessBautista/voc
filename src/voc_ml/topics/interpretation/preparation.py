"""Verified G3/R2 inputs and common evidence for an interpretation experiment.

No provider, tracking client, cache, or persistent run is created here. Paths in
configuration files are resolved relative to that file, never via latest.
"""

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from voc.embeddings.shared.contracts import fingerprint, require_digest
from voc.topics import read_topics
from voc.topics.representation import REPRESENTATION_SCHEMA_V2
from voc.topics.scope import SCHEMA_V2


@dataclass(frozen=True)
class InputConfig:
    grouping_directory: Path
    grouping_manifest_sha256: str
    representation_directory: Path
    representation_manifest_sha256: str
    max_reviews: int = 3
    max_chars: int = 900
    max_topics: int = 12

    def __post_init__(self):
        require_digest(self.grouping_manifest_sha256, "grouping_manifest_sha256")
        require_digest(
            self.representation_manifest_sha256, "representation_manifest_sha256"
        )
        for field, low, high in (
            ("max_reviews", 1, 10),
            ("max_chars", 50, 2000),
            ("max_topics", 1, 24),
        ):
            value = getattr(self, field)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{field} must be an integer in [{low}, {high}]")


def load_inputs(path):
    path = Path(path)
    with path.open("rb") as stream:
        values = tomllib.load(stream)
    required = {
        "grouping_directory",
        "grouping_manifest_sha256",
        "representation_directory",
        "representation_manifest_sha256",
    }
    if (
        not required <= values.keys()
        or values.keys() - InputConfig.__dataclass_fields__.keys()
    ):
        raise ValueError("Invalid interpretation input configuration fields")
    for field in ("grouping_directory", "representation_directory"):
        if not isinstance(values[field], str) or not values[field].strip():
            raise ValueError(f"{field} must be a nonempty path")
        values[field] = (path.parent / values[field]).resolve()
    return InputConfig(**values)


def _read_pair(config, reader):
    grouping = reader(
        config.grouping_directory, manifest_sha256=config.grouping_manifest_sha256
    )
    representation = reader(
        config.representation_directory,
        manifest_sha256=config.representation_manifest_sha256,
    )
    g, r = grouping.info, representation.info
    if (
        g["schema_version"] != SCHEMA_V2
        or r["schema_version"] != REPRESENTATION_SCHEMA_V2
    ):
        raise ValueError(
            "Interpretation preparation requires v2 grouping and representation"
        )
    if (
        g["input"]["selection"]["mode"] != "full"
        or g["configuration"]["model"]["cluster_selection_method"] != "leaf"
        or representation.execution["representation_config"]["method"] != "keybert_mmr"
    ):
        raise ValueError("Expected full-corpus leaf grouping and KeyBERTInspired + MMR")
    if (
        representation.execution["parent_manifest_sha256"]
        != config.grouping_manifest_sha256
        or representation.execution["parent_manifest"] != g
    ):
        raise ValueError("Representation does not descend from the supplied grouping")
    for field in ("input", "configuration", "counts"):
        if g[field] != r[field]:
            raise ValueError("Grouping and representation have different scope")
    # The public reader validates each file and the topic skeleton against the
    # embedded parent. Comparing verified file metadata proves preservation.
    for name in ("selection.parquet", "assignments.parquet", "coordinates.npy"):
        if g["files"][name] != r["files"][name]:
            raise ValueError("Representation changed grouping content")
    if [(t["topic_id"], t["size"], t["evidence_ids"]) for t in grouping.topics] != [
        (t["topic_id"], t["size"], t["evidence_ids"]) for t in representation.topics
    ]:
        raise ValueError("Representation changed topic evidence")
    return grouping, representation


def _summary(config, grouping, representation):
    identity = {
        "grouping_run_id": grouping.info["run_id"],
        "grouping_manifest_sha256": config.grouping_manifest_sha256,
        "representation_run_id": representation.info["run_id"],
        "representation_manifest_sha256": config.representation_manifest_sha256,
        "source_input_sha256": fingerprint(grouping.info["input"]),
        "dataset": grouping.info["input"]["dataset"],
        "selection": grouping.info["input"]["selection"],
        "embedding_release_id": grouping.info["input"]["embedding_release_id"],
        "evidence_policy": {
            "name": "saved-evidence-then-record-id-distinct-text-prefix-v1",
            "max_reviews": config.max_reviews,
            "max_chars": config.max_chars,
            "max_topics": config.max_topics,
            "character_unit": "unicode-code-points",
        },
    }
    return {
        "schema_version": "voc-topic-interpretation-inputs-v1",
        "state": "inputs_verified",
        "identity": identity,
        "input_sha256": fingerprint(identity),
        "counts": grouping.info["counts"],
        "preserved": ["reviews", "assignments", "coordinates", "topic_evidence"],
        "execution_ready": False,
        "pending": ["case_partitions", "prompts", "provider_budget", "producer"],
        "model_requests": 0,
        "run_created": False,
    }


def inspect_inputs(config, *, reader=read_topics):
    """Validate both pinned releases, returning metadata without review text."""
    grouping, representation = _read_pair(config, reader)
    return _summary(config, grouping, representation)


def prepare_cases(config, topic_ids, *, reader=read_topics):
    """Prepare R0/R2 cases with identical ordered evidence, without sending it.

    R0 is the initial c-TF-IDF representation of this G3, not the G0 grouping.
    Distinctness is enforced on both full text and the actual truncated excerpt.
    """
    topic_ids = list(topic_ids)
    if (
        not 1 <= len(topic_ids) <= config.max_topics
        or any(type(tid) is not int or tid < 0 for tid in topic_ids)
        or len(set(topic_ids)) != len(topic_ids)
    ):
        raise ValueError("Select distinct nonnegative topic IDs within max_topics")
    grouping, representation = _read_pair(config, reader)
    return _prepare_cases(config, topic_ids, grouping, representation)


def _prepare_cases(config, topic_ids, grouping, representation):
    topic_ids = list(topic_ids)
    if (
        not 1 <= len(topic_ids) <= config.max_topics
        or any(type(tid) is not int or tid < 0 for tid in topic_ids)
        or len(set(topic_ids)) != len(topic_ids)
    ):
        raise ValueError("Select distinct nonnegative topic IDs within max_topics")
    topics = {t["topic_id"]: t for t in grouping.topics}
    refined = {t["topic_id"]: t for t in representation.topics}
    if any(tid not in topics for tid in topic_ids):
        raise ValueError("Unknown topic ID")
    texts = grouping.reviews.set_index("record_id").model_text.to_dict()
    members = grouping.assignments.groupby("topic_id").record_id.agg(list).to_dict()
    cases = []
    for tid in topic_ids:
        topic = topics[tid]
        evidence, seen_texts, seen_excerpts = [], set(), set()
        ordered = dict.fromkeys([*topic["evidence_ids"], *sorted(members[tid])])
        for rid in ordered:
            text = texts[rid]
            excerpt = text[: config.max_chars]
            if text in seen_texts or excerpt in seen_excerpts:
                continue
            seen_texts.add(text)
            seen_excerpts.add(excerpt)
            evidence.append(
                {
                    "label": f"E{len(evidence) + 1}",
                    "record_id": rid,
                    "text": excerpt,
                    "source_text_sha256": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    "source_characters": len(text),
                    "truncated": len(text) > config.max_chars,
                }
            )
            if len(evidence) == config.max_reviews:
                break
        if not evidence:
            raise ValueError("Topic has no usable evidence")
        cases.append(
            {
                "topic_id": tid,
                "size": topic["size"],
                "evidence": evidence,
                "evidence_sha256": fingerprint(evidence),
                "keywords": {"R0": topic["keywords"], "R2": refined[tid]["keywords"]},
            }
        )
    result = _summary(config, grouping, representation)
    result.update(
        state="cases_prepared",
        cases=cases,
        cases_sha256=fingerprint(cases),
        selected_topics=len(cases),
        partition="unassigned",
    )
    return result
