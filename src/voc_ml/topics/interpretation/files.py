"""Local configuration, exposure and draft-plan adapters, with no overwrite."""

import csv
import hashlib
import io
import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from voc.embeddings.shared.contracts import canonical_json, fingerprint, require_digest

from .preparation import InputConfig, load_inputs
from .prompts import Generation, Prompt


@dataclass(frozen=True)
class StudyConfig:
    inputs: InputConfig
    known_topics: tuple
    seed: int
    generation: Generation
    prompts: dict
    exposure_files: tuple
    max_provider_attempts: int
    proposed_max_cost_usd: float


def verified_bytes(path, sha256):
    require_digest(sha256)
    path = Path(path)
    if path.is_symlink() or not 0 < path.stat().st_size <= 16 * 2**20:
        raise ValueError("Invalid input file size or symlink")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("Input file checksum mismatch")
    return data


def load_study(path):
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    fields = {
        "inputs",
        "seed",
        "known_development_topics",
        "candidate_model",
        "temperature",
        "max_tokens",
        "max_provider_attempts",
        "proposed_max_cost_usd",
        "prompts",
        "exposure_files",
    }
    if set(data) != fields or set(data["prompts"]) != {"P0", "P1", "P2"}:
        raise ValueError("Unexpected study or prompt configuration fields")
    prompts = {}
    for variant, spec in data["prompts"].items():
        if set(spec) != {"path", "version", "sha256"}:
            raise ValueError("Unexpected prompt fields")
        prompts[variant] = Prompt(
            variant,
            spec["version"],
            verified_bytes(path.parent / spec["path"], spec["sha256"]).decode("utf-8"),
        )
    if not prompts["P1"].text.startswith(prompts["P0"].text + "\n\n") or not prompts[
        "P2"
    ].text.startswith(prompts["P1"].text + "\n\n"):
        raise ValueError("P1/P2 must preserve the baseline instructions")
    if (
        type(data["max_provider_attempts"]) is not int
        or data["max_provider_attempts"] != 72
    ):
        raise ValueError("This draft comparison requires a 72-attempt upper bound")
    cost = data["proposed_max_cost_usd"]
    if type(cost) not in (float, int) or not math.isfinite(cost) or cost <= 0:
        raise ValueError("Invalid proposed cost ceiling")
    exposures = []
    for spec in data["exposure_files"]:
        if set(spec) != {"path", "sha256"}:
            raise ValueError("Unexpected exposure source fields")
        require_digest(spec["sha256"])
        exposures.append((path.parent / spec["path"], spec["sha256"]))
    if not exposures:
        raise ValueError("Register the historical exposure sources")
    return StudyConfig(
        load_inputs(path.parent / data["inputs"]),
        tuple(data["known_development_topics"]),
        data["seed"],
        Generation(data["candidate_model"], data["temperature"], data["max_tokens"]),
        prompts,
        tuple(exposures),
        data["max_provider_attempts"],
        cost,
    )


def read_exposure(files, population_ids):
    """Collect matching record IDs anywhere in pinned JSON/CSV evidence exports."""
    population_ids = set(population_ids)
    found, sources = set(), []

    def visit(value):
        if isinstance(value, str):
            if value in population_ids:
                found.add(value)
        elif isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path, digest in files:
        raw = verified_bytes(path, digest)
        prior = len(found)
        if path.suffix == ".json":
            visit(json.loads(raw))
        elif path.suffix == ".csv":
            visit(list(csv.DictReader(io.StringIO(raw.decode("utf-8")))))
        else:
            raise ValueError("Unsupported exposure source format")
        sources.append(
            {
                "sha256": digest,
                "format": path.suffix,
                "new_record_ids": len(found) - prior,
            }
        )
    if not found:
        raise ValueError("Exposure sources contain no matching records")
    return found, sources


def write_draft(path, draft):
    """Explicit local export. Existing output is never replaced or replayed."""
    if fingerprint(draft["plan"]) != draft["plan_sha256"]:
        raise ValueError("Draft checksum differs")
    payload = canonical_json(draft)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as f:
        f.write(payload)


def read_draft(path):
    """Detect changes to a local draft before development review."""
    path = Path(path)
    if path.is_symlink() or not 0 < path.stat().st_size <= 16 * 2**20:
        raise ValueError("Invalid draft file")
    draft = json.loads(path.read_bytes())
    if (
        set(draft) != {"schema_version", "plan", "plan_sha256"}
        or draft["schema_version"] != "voc-topic-interpretation-draft-v1"
    ):
        raise ValueError("Unsupported draft")
    require_digest(draft["plan_sha256"])
    if fingerprint(draft["plan"]) != draft["plan_sha256"]:
        raise ValueError("Draft checksum differs")
    return draft
