"""Reuse the existing analysis pipeline; freeze sampling separately from model fits."""

import json
import sys
import time
from pathlib import Path

import pandas as pd

from voc.analysis.local_inputs import load_local_config
from voc.datasets.manifest import file_hash
from voc.embeddings.contracts import fingerprint, text_hash
from voc.relevance import decisions, load_model
from voc_ml.analysis import artifacts, producer
from voc_ml.analysis.config import AnalysisConfig, SelectionConfig
from voc_ml.analysis.inputs import _positions, _strata, pin_inputs, select_record_ids
from voc_ml.sentiment import load_config as load_sentiment


def choose_sample(full, annotations, *, start, end, size=1000, seed=42):
    """Sample records proportionally before filtering; exclude all known annotation texts."""
    full.validate()
    dates = pd.to_datetime(
        full.reviews.record_date, format="mixed", utc=True, errors="coerce"
    )
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    if lower >= upper:
        raise ValueError("Sampling window must be nonempty")
    known = set(annotations.record_id)
    lookup = full.reviews.set_index("record_id").model_text
    if not known <= set(lookup.index):
        raise ValueError("Annotation ID absent from frozen corpus")
    hashes = set(lookup.loc[list(known)].map(text_hash))
    overlap = full.reviews.model_text.map(text_hash).isin(
        hashes
    ) | full.reviews.record_id.isin(known)
    in_window = dates.ge(lower) & dates.lt(upper)
    eligible = full.reviews.loc[in_window & ~overlap].reset_index(drop=True)
    groups = _strata(eligible)
    positions = _positions(
        groups, len(eligible), SelectionConfig("stratified", size, seed)
    )
    chosen = eligible.iloc[positions]
    pinned = select_record_ids(full, chosen.record_id.tolist())
    chosen_groups = _strata(chosen)
    coverage = [
        {
            "platform": p,
            "month": m,
            "eligible": len(rows),
            "selected": len(chosen_groups.get((p, m), [])),
        }
        for (p, m), rows in groups.items()
    ]
    weekly = chosen.assign(
        week=pd.to_datetime(chosen.record_date, format="mixed", utc=True).dt.strftime(
            "%G-W%V"
        )
    )
    return pinned, {
        "window_start_inclusive": start,
        "window_end_exclusive": end,
        "size": size,
        "seed": seed,
        "policy": "platform-month-proportional-largest-remainder-v1",
        "unit": "records; duplicate texts retained except annotation overlap",
        "in_window": int(in_window.sum()),
        "annotation_overlap_excluded": int((in_window & overlap).sum()),
        "eligible": len(eligible),
        "coverage": coverage,
        "weekly_selected": weekly.groupby(["platform", "week"])
        .size()
        .rename("rows")
        .reset_index()
        .to_dict("records"),
        "independent_sentiment_test": False,
        "reason": "sentiment training record IDs unavailable",
        "near_duplicate_screening": False,
    }


def prepare(
    local_inputs,
    annotations,
    relevance_model,
    relevance_sha256,
    output,
    *,
    start,
    end,
    size=1000,
    seed=42,
):
    root = Path(output).resolve()
    if root.exists():
        raise ValueError("Experiment directory already exists")
    full = pin_inputs(source="local", local_inputs=load_local_config(local_inputs))
    labels = pd.read_csv(annotations, keep_default_na=False)
    sample, policy = choose_sample(
        full, labels, start=start, end=end, size=size, seed=seed
    )
    model = load_model(
        relevance_model,
        sha256=relevance_sha256,
        profile_id=full.info["embedding"]["profile_id"],
    )
    root.mkdir(parents=True)
    reference = artifacts.write_input(sample, root / "sample")
    verdict = decisions(sample.reviews, sample.vectors, model)
    verdict.to_csv(root / "relevance.csv", index=False)
    alternative = select_record_ids(
        sample, verdict.loc[verdict.keep, "record_id"].tolist()
    )
    alternative_reference = artifacts.write_input(alternative, root / "alternative")
    # The same grouping/representation settings apply to both systems.
    grouping = AnalysisConfig()
    grouping.validate_row_count(len(alternative.reviews))
    configs = {}
    for name, path in [
        ("base", "config/analysis/sentiment.toml"),
        ("alternative", "config/analysis/sentiment-stores-v3-all-sources.toml"),
    ]:
        source = Path(path)
        copied = root / (name + "-sentiment.toml")
        copied.write_bytes(source.read_bytes())
        load_sentiment(copied)
        configs[name] = {"path": str(copied), "sha256": file_hash(copied)}
    representation = root / "representation.toml"
    representation.write_text(
        'method = "keybert_mmr"\ntop_n_words = 10\ncandidate_words = 30\ndiversity = 0.3\nnr_repr_docs = 5\nnr_samples = 500\nbatch_size = 32\n'
    )
    protocol = {
        "schema_version": "voc-diffusion-comparison-v1",
        "sample": reference,
        "alternative_input": alternative_reference,
        "sampling": policy,
        "local_inputs": str(Path(local_inputs).resolve()),
        "local_inputs_sha256": file_hash(local_inputs),
        "annotations_sha256": file_hash(annotations),
        "relevance_model_sha256": relevance_sha256,
        "relevance_decisions_sha256": file_hash(root / "relevance.csv"),
        "grouping": grouping.as_dict(),
        "sentiment": configs,
        "representation": {
            "path": str(representation),
            "sha256": file_hash(representation),
        },
        "initial_rows": len(sample.reviews),
        "retained_rows": len(alternative.reviews),
        "llm_requests": 0,
        "fit_limit": 2,
        "selection_basis": "explicit calendar window fixed before fitting",
        "sentiment_scope": "team chose stores v3 for all sources; social quality not established",
    }
    protocol["protocol_id"] = fingerprint(protocol)
    artifacts.write_json(root / "protocol.json", protocol)
    return protocol


def load_protocol(directory):
    root = Path(directory).resolve()
    value = json.loads((root / "protocol.json").read_bytes())
    if (
        fingerprint({k: v for k, v in value.items() if k != "protocol_id"})
        != value["protocol_id"]
    ):
        raise ValueError("Experiment protocol checksum mismatch")
    for ref in [value["sample"], value["alternative_input"]]:
        artifacts.read_input(ref)
    for ref in [*value["sentiment"].values(), value["representation"]]:
        artifacts.checked_path(ref)
    if file_hash(root / "relevance.csv") != value["relevance_decisions_sha256"]:
        raise ValueError("Relevance decisions changed")
    if file_hash(value["local_inputs"]) != value["local_inputs_sha256"]:
        raise ValueError("Local input configuration changed")
    return value


def run(directory, system):
    from voc_ml.analysis.representation import execute_representation

    if system not in {"base", "alternative"}:
        raise ValueError("Unknown system")
    root = Path(directory).resolve()
    protocol = load_protocol(root)
    receipt = root / (system + "-run.json")
    if receipt.exists():
        raise ValueError("Attempt already exists; inspect instead of refitting")
    started = time.monotonic()
    state = {
        "system": system,
        "protocol_id": protocol["protocol_id"],
        "state": "running",
        "phase": "analysis",
    }

    def save():
        state["wall_seconds"] = time.monotonic() - started
        temp = receipt.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2) + "\n")
        temp.replace(receipt)

    save()
    try:
        ref = protocol["sample" if system == "base" else "alternative_input"]
        print(
            f"{system}: fitting topics and classifying {len(artifacts.read_input(ref).reviews)} reviews",
            file=sys.stderr,
            flush=True,
        )
        run_state = producer.create_run(
            source="local",
            local_inputs=protocol["local_inputs"],
            pinned_input=ref,
            config=AnalysisConfig.from_dict(protocol["grouping"]),
            sentiment_config=load_sentiment(protocol["sentiment"][system]["path"]),
        )
        state["run_id"] = run_state["run_id"]
        save()
        run_state = producer.execute_run(run_state["run_id"])
        if run_state["state"] != "completed":
            raise RuntimeError(f"Analysis failed; inspect run {run_state['run_id']}")
        state["analysis_id"] = run_state["analysis_id"]
        state["phase"] = "representation"
        save()
        print(
            f"{system}: refining keywords with KeyBERTInspired + MMR",
            file=sys.stderr,
            flush=True,
        )
        represented = execute_representation(
            run_state["analysis_id"],
            protocol["representation"]["path"],
            local_files_only=True,
        )
        state["representation"] = represented
        if represented["state"] != "completed":
            raise RuntimeError("Representation failed; inspect saved attempt")
        state.update(state="completed", phase="complete", llm_requests=0)
        save()
        return state
    except Exception as error:
        state.update(state="failed", error=str(error))
        save()
        raise
