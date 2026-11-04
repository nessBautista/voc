"""Boundaries protecting comparison, reserve isolation and portable contracts."""

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from click.testing import CliRunner

from voc.embeddings.shared.contracts import fingerprint
from voc.topics.interpretation import normalize_response
from voc_ml.topics.interpretation.cli import interpretation
from voc_ml.topics.interpretation.files import (
    read_draft,
    read_exposure,
    verified_bytes,
    write_draft,
)
from voc_ml.topics.interpretation.prompts import Generation, Prompt, build_request
from voc_ml.topics.interpretation.selection import normalized, propose_partitions


@pytest.fixture
def case():
    evidence = [
        {
            "label": "E1",
            "record_id": "private-record",
            "text": "No puedo entrar",
            "truncated": False,
        }
    ]
    return {
        "topic_id": 0,
        "evidence": evidence,
        "evidence_sha256": fingerprint(evidence),
        "keywords": {
            "R0": [{"term": "entrar", "weight": 0.3}],
            "R2": [{"term": "acceso", "weight": 0.8}],
        },
    }


@pytest.fixture
def response():
    return {
        "name": "Acceso",
        "topic_description": "Se informa una dificultad de acceso.",
        "assessment": "single_feature",
        "proposed_feature": "Acceso",
        "supporting_evidence": ["E1"],
    }


def test_normalization_keeps_citations_local(case, response):
    result = normalize_response(response, case["evidence"])
    assert result["supporting_review_ids"] == ["private-record"]
    assert "supporting_evidence" not in result


@pytest.mark.parametrize(
    "changes",
    [
        {"supporting_evidence": ["E9"]},
        {"supporting_evidence": ["E1", "E1"]},
        {"supporting_evidence": []},
        {"assessment": "mixed"},
        {"proposed_feature": None},
        {"name": " "},
        {"extra": "field"},
        {"assessment": "coherent"},
        {"topic_description": "a" * 2001},
    ],
)
def test_invalid_response_rejected(case, response, changes):
    with pytest.raises(ValueError):
        normalize_response({**response, **changes}, case["evidence"])


def test_unclear_without_forced_feature(case, response):
    result = normalize_response(
        {**response, "assessment": "unclear", "proposed_feature": None},
        case["evidence"],
    )
    assert result["proposed_feature"] is None


def test_requests_keep_evidence_fixed_and_strip_private_ids(case):
    p0 = Prompt("P0", "v0", "Baseline")
    p1 = Prompt("P1", "v1", "Baseline\n\nFocus")
    gen = Generation("candidate/model")
    reqs = [
        build_request(case, rep, prompt, gen)
        for rep, prompt in [("R0", p0), ("R2", p0), ("R2", p1)]
    ]
    payloads = [
        json.loads(r["request"]["body"]["messages"][1]["content"]) for r in reqs
    ]
    assert payloads[0]["evidence"] == payloads[1]["evidence"] == payloads[2]["evidence"]
    assert payloads[1] == payloads[2]
    assert len({r["request_sha256"] for r in reqs}) == 3
    assert "private-record" not in json.dumps(reqs[0]["request"]["body"])
    assert reqs[0]["provider_ready"] is False
    altered = deepcopy(case)
    altered["evidence"][0]["text"] = "changed"
    with pytest.raises(ValueError, match="identity"):
        build_request(altered, "R2", p0, gen)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"temperature": float("nan")},
        {"temperature": True},
        {"max_tokens": True},
        {"model": ""},
    ],
)
def test_invalid_generation(kwargs):
    with pytest.raises(ValueError):
        Generation(**{"model": "candidate/model", **kwargs})


def sample_bundle():
    topics, records, assignments = [], [], []
    for tid in range(60):
        size = [10, 25, 55][tid % 3]
        topics.append({"topic_id": tid, "size": size})
        for j in range(2):
            rid = f"r{tid}-{j}"
            text = hashlib.sha256(rid.encode()).hexdigest()
            records.append({"record_id": rid, "model_text": text})
            assignments.append({"record_id": rid, "topic_id": tid})
    return SimpleNamespace(
        topics=topics,
        reviews=pd.DataFrame(records),
        assignments=pd.DataFrame(assignments),
    )


def test_selection_repeatability_and_exposure_exclusion():
    bundle = sample_bundle()
    first = propose_partitions(bundle, [0, 1, 2, 3, 4, 5], {"r6-0"})
    assert first == propose_partitions(bundle, [0, 1, 2, 3, 4, 5], {"r6-0"})
    assert len(first["development"]) == len(first["reserve"]) == 12
    assert not set(first["development"]) & set(first["reserve"])
    assert 6 not in first["reserve"]
    assert first["audit"]["reserve_size_bands"] == {"small": 4, "medium": 4, "large": 4}
    assert normalized("ÁCCESO,   móvil!") == "acceso movil"


def test_any_member_duplicate_excludes_reserve_topic():
    bundle = sample_bundle()
    first = propose_partitions(bundle, [0, 1, 2, 3, 4, 5], set())
    target = first["reserve"][0]
    text = bundle.reviews.loc[bundle.reviews.record_id == "r0-0", "model_text"].iloc[0]
    bundle.reviews.loc[bundle.reviews.record_id == f"r{target}-1", "model_text"] = text
    second = propose_partitions(bundle, [0, 1, 2, 3, 4, 5], set())
    assert target not in second["reserve"]
    assert second["audit"]["rejected_candidates"]["normalized_text_overlap"] >= 1


def test_near_duplicates_excluded_without_relaxation():
    bundle = sample_bundle()
    first = propose_partitions(bundle, [0, 1, 2, 3, 4, 5], set())
    target = first["reserve"][0]
    text = bundle.reviews.loc[bundle.reviews.record_id == "r0-0", "model_text"].iloc[0]
    bundle.reviews.loc[bundle.reviews.record_id == f"r{target}-1", "model_text"] = (
        text + "xx"
    )
    second = propose_partitions(bundle, [0, 1, 2, 3, 4, 5], set())
    assert target not in second["reserve"]
    assert second["audit"]["rejected_candidates"]["near_duplicate_overlap"] >= 1
    with pytest.raises(ValueError, match="Insufficient clean"):
        propose_partitions(bundle, [0, 1, 2, 3, 4, 5], set(bundle.reviews.record_id))


def test_exposure_sources_are_pinned_and_reconciled(tmp_path):
    path = tmp_path / "exposure.json"
    path.write_text(
        json.dumps(
            {
                "examples": [{"record_id": "r1", "model_text": "not an ID"}],
                "unassigned_ids": ["r2"],
            }
        )
    )
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    ids, sources = read_exposure([(path, sha)], {"r1", "r2", "r3"})
    assert ids == {"r1", "r2"} and sources[0]["new_record_ids"] == 2
    path.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        verified_bytes(path, sha)


def test_draft_export_never_overwrites_and_review_hides_reserve(tmp_path):
    plan = {
        "cases": {
            "development": [{"topic_id": 1, "text": "dev"}],
            "reserve": [{"text": "DO_NOT_SHOW_RESERVE"}],
        }
    }
    draft = {
        "schema_version": "voc-topic-interpretation-draft-v1",
        "plan": plan,
        "plan_sha256": fingerprint(plan),
    }
    path = tmp_path / "plan.json"
    write_draft(path, draft)
    assert read_draft(path) == draft
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_draft(path, draft)
    assert path.read_bytes() == before
    result = CliRunner().invoke(interpretation, ["review", "--plan", str(path)])
    assert result.exit_code == 0
    assert "DO_NOT_SHOW_RESERVE" not in result.output
    draft["plan"]["cases"]["development"][0]["text"] = "changed"
    path.write_text(json.dumps(draft))
    with pytest.raises(ValueError, match="checksum"):
        read_draft(path)


def test_portable_contract_does_not_import_producer_or_sdk():
    source = """
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'voc_ml', 'click', 'openai', 'torch', 'zenml', 'mlflow'}:
   raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from voc.topics.interpretation import response_schema
assert response_schema(['E1'])['properties']['supporting_evidence']['items']['enum'] == ['E1']
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve())},
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("labels", [None, "E1", [1], [""], ["E1", "E1"]])
def test_schema_rejects_invalid_evidence_labels(labels):
    from voc.topics.interpretation import response_schema

    with pytest.raises(ValueError):
        response_schema(labels)


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        [{}],
        [{"label": "E1", "record_id": ""}],
        [{"label": "E1", "record_id": "r"}, {"label": "E2", "record_id": "r"}],
    ],
)
def test_response_rejects_invalid_local_mapping(response, evidence):
    with pytest.raises(ValueError, match="mapping"):
        normalize_response(response, evidence)


def test_config_pins_prompt_text_and_preserves_baseline(tmp_path, monkeypatch):
    from voc_ml.topics.interpretation import files

    monkeypatch.setattr(files, "load_inputs", lambda path: "verified-inputs")
    texts = {
        "P0": "Baseline",
        "P1": "Baseline\n\nFeature",
        "P2": "Baseline\n\nFeature\n\nEvidence",
    }
    config = tmp_path / "study.toml"

    def write_config():
        parts = [
            'inputs = "inputs.toml"',
            "seed = 42",
            "known_development_topics = [0, 1, 2, 3, 4, 5]",
            'candidate_model = "candidate/model"',
            "temperature = 0.0",
            "max_tokens = 800",
            "max_provider_attempts = 72",
            "proposed_max_cost_usd = 1.0",
        ]
        for variant, text in texts.items():
            (tmp_path / f"{variant}.txt").write_text(text)
            digest = hashlib.sha256(text.encode()).hexdigest()
            parts += [
                f"[prompts.{variant}]",
                f'path = "{variant}.txt"',
                f'version = "{variant}-v1"',
                f'sha256 = "{digest}"',
            ]
        parts += [
            "[[exposure_files]]",
            'path = "exposure.json"',
            f'sha256 = "{"a" * 64}"',
        ]
        config.write_text("\n".join(parts))

    write_config()
    loaded = files.load_study(config)
    assert loaded.inputs == "verified-inputs"
    assert loaded.prompts["P0"].text == "Baseline"
    (tmp_path / "P1.txt").write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        files.load_study(config)
    texts["P1"] = "Replacement baseline"
    write_config()
    with pytest.raises(ValueError, match="preserve"):
        files.load_study(config)


@pytest.mark.parametrize("version,text", [(1, "text"), ("", "text"), ("v1", None)])
def test_prompt_requires_named_version_and_text(version, text):
    with pytest.raises(ValueError, match="prompt"):
        Prompt("P0", version, text)
