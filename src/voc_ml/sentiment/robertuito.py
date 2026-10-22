"""Whole-review RoBERTuito sentiment with durable batch reuse and progress.

Inputs are workable model_text values, never embedding_text or generated topic
wording. Only successful model predictions enter the cache. Model failures stop
execution, retaining completed batches for the next attempt.
"""

import os
import platform
import re
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import pandas as pd

from voc.analysis.contracts import SENTIMENT_STATUSES, ReviewSentiment
from voc.embeddings.contracts import canonical_json, fingerprint, text_hash
from voc.paths import data_root

from .cache import PredictionCache, prediction

MODEL = "pysentimiento/robertuito-sentiment-analysis"
REVISION = "a2cc0f67ebd705c55191e25a05ba23d885fcc09b"
LABEL_MAP = {"NEG": "negative", "NEU": "neutral", "POS": "positive"}


@dataclass(frozen=True)
class SentimentConfig:
    model: str = MODEL
    revision: str = REVISION
    max_length: int = 128
    batch_size: int = 16

    def __post_init__(self):
        if self.model != MODEL:
            raise ValueError(
                "This adapter supports the RoBERTuito sentiment model only"
            )
        if not isinstance(self.revision, str) or not re.fullmatch(
            r"[0-9a-f]{40}", self.revision
        ):
            raise ValueError("revision must be a pinned 40-character commit")
        if type(self.max_length) is not int or not 4 <= self.max_length <= 128:
            raise ValueError("max_length must be an integer from 4 to 128")
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 256:
            raise ValueError("batch_size must be an integer from 1 to 256")


def load_config(path):
    with Path(path).open("rb") as stream:
        value = tomllib.load(stream)
    if set(value) - set(SentimentConfig.__dataclass_fields__):
        raise ValueError("Unknown sentiment configuration key")
    return SentimentConfig(**value)


def classifier_descriptor(config):
    config.__post_init__()
    return {
        "adapter": "robertuito-cpu-f32-eager-threads4-v1",
        "model": config.model,
        "revision": config.revision,
        "max_length": config.max_length,
        "truncation_side": "right",
        "preprocessing": "pysentimiento.preprocess_tweet(lang=es)-defaults-v1",
        "label_mapping": LABEL_MAP,
        "software": {
            name: version(name)
            for name in (
                "pysentimiento",
                "emoji",
                "transformers",
                "tokenizers",
                "torch",
                "numpy",
                "huggingface-hub",
            )
        }
        | {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "system": platform.system(),
        },
    }


def _prepare(text):
    from pysentimiento.preprocessing import preprocess_tweet

    return preprocess_tweet(text, lang="es")


class RobertuitoClassifier:
    """Pinned local CPU model. Network access must be explicitly enabled by caller."""

    def __init__(self, config, model_dir, *, local_files_only=True):
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        config.__post_init__()
        self.config = config
        self.torch = torch
        snapshot = Path(
            snapshot_download(
                repo_id=config.model,
                revision=config.revision,
                cache_dir=str(model_dir),
                local_files_only=local_files_only,
                token=False,
                allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
            )
        )
        if snapshot.name != config.revision:
            raise ValueError("Resolved sentiment model differs from pinned revision")
        options = {"local_files_only": True, "trust_remote_code": False, "token": False}
        self.tokenizer = AutoTokenizer.from_pretrained(str(snapshot), **options)
        self.tokenizer.truncation_side = "right"
        self.tokenizer.padding_side = "right"
        self.model = (
            AutoModelForSequenceClassification.from_pretrained(
                str(snapshot),
                use_safetensors=True,
                attn_implementation="eager",
                **options,
            )
            .to("cpu")
            .float()
            .eval()
        )
        native = self.model.config.id2label
        if set(native) != {0, 1, 2} or set(native.values()) != set(LABEL_MAP):
            raise ValueError("Unexpected sentiment model label mapping")
        self.mapping = {index: LABEL_MAP[label] for index, label in native.items()}
        capacity = (
            self.model.config.max_position_embeddings
            - self.model.config.pad_token_id
            - 1
        )
        if config.max_length > capacity:
            raise ValueError("Sentiment max_length exceeds the model position limit")

    def predict(self, texts):
        lengths = self.tokenizer(
            texts,
            padding=False,
            truncation=False,
            add_special_tokens=True,
            return_length=True,
            verbose=False,
        )["length"]
        inputs = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.config.max_length,
            return_tensors="pt",
        )
        previous = self.torch.get_num_threads()
        try:
            self.torch.set_num_threads(4)
            with self.torch.inference_mode():
                scores = self.model(**inputs).logits.softmax(dim=-1).cpu().tolist()
        finally:
            self.torch.set_num_threads(previous)
        if len(scores) != len(texts) or len(lengths) != len(texts):
            raise ValueError("Unexpected sentiment batch size")
        results = []
        for values, length in zip(scores, lengths, strict=True):
            if len(values) != 3:
                raise ValueError("Expected three sentiment scores")
            results.append(
                prediction(
                    {self.mapping[i]: p for i, p in enumerate(values)}, int(length)
                )
            )
        return results


@dataclass(frozen=True)
class SentimentResult:
    sentiments: tuple[ReviewSentiment, ...]
    diagnostics: tuple[dict, ...]
    report: dict


class SentimentRunError(RuntimeError):
    def __init__(self, report):
        self.report = report
        super().__init__(
            f"Sentiment run failed; completed predictions are reusable. Inspect {report['progress_path']}"
        )


def classify_reviews(
    reviews,
    config=None,
    *,
    cache_path=None,
    model_dir=None,
    run_root=None,
    local_files_only=True,
    classifier_factory=None,
):
    """Classify a selected DataFrame, preserving order and every unique review ID.

    The injectable classifier factory receives (config, model_dir,
    local_files_only=...) and returns an object with predict(prepared_texts).
    Each returned Prediction is validated before a whole batch is committed.
    Factories must implement this adapter's semantics and must not share a real
    cache with test classifiers. Exclusions/preprocessing failures stay visible
    in results but are not cached as predictions.
    """
    config = SentimentConfig() if config is None else config
    if not isinstance(config, SentimentConfig):
        raise TypeError("Expected SentimentConfig")
    config.__post_init__()
    if (
        not isinstance(reviews, pd.DataFrame)
        or reviews.empty
        or not reviews.columns.is_unique
        or not {"record_id", "model_text"} <= set(reviews.columns)
    ):
        raise ValueError("Expected nonempty reviews with record_id and model_text")
    ids = reviews.record_id.tolist()
    if any(not isinstance(rid, str) or not rid.strip() for rid in ids) or len(
        set(ids)
    ) != len(ids):
        raise ValueError("Review IDs must be nonblank and unique")
    if type(local_files_only) is not bool:
        raise ValueError("local_files_only must be boolean")
    descriptor = classifier_descriptor(config)
    cache_path = (
        Path(cache_path)
        if cache_path is not None
        else data_root() / "cache/sentiment/predictions.sqlite"
    )
    model_dir = (
        Path(model_dir)
        if model_dir is not None
        else Path(
            os.environ.get("VOC_SENTIMENT_MODEL_DIR", data_root() / "models/sentiment")
        )
    )
    run_root = (
        Path(run_root) if run_root is not None else data_root() / "sentiment/runs"
    )
    run_id = str(uuid4())
    directory = run_root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    progress_path = directory / "progress.json"
    report = {
        "schema_version": "voc-sentiment-run-v1",
        "run_id": run_id,
        "state": "running",
        "configuration": asdict(config),
        "descriptor": descriptor,
        "classifier_id": fingerprint(descriptor),
        "progress_path": str(progress_path),
        "cache_path": str(cache_path),
        "started_at": datetime.now(UTC).isoformat(),
    }
    results, diagnostics, prepared_by_hash, row_hashes = {}, {}, {}, {}
    computed, reused = set(), set()
    status_counts = dict.fromkeys(SENTIMENT_STATUSES, 0)
    truncated_rows = 0

    def save_progress(state, error=None):
        report.update(
            state=state,
            updated_at=datetime.now(UTC).isoformat(),
            counts={
                "rows": len(ids),
                "unique_inputs": len(prepared_by_hash),
                "computed_unique": len(computed),
                "reused_unique": len(reused),
                "pending_unique": len(prepared_by_hash) - len(computed) - len(reused),
                "pending_rows": len(ids) - len(results),
                "truncated_rows": truncated_rows,
                **status_counts,
            },
            error=error,
        )
        payload = dict(report)
        # Batch checkpoints remain small even for large selections. Full detail
        # is written once on completion/failure; successful batches live in SQLite.
        if state != "running":
            payload.update(
                sentiments=[asdict(results[rid]) for rid in ids if rid in results],
                diagnostics=[diagnostics[rid] for rid in ids if rid in diagnostics],
                pending_record_ids=[rid for rid in ids if rid not in results],
            )
        temporary = progress_path.with_suffix(".tmp")
        temporary.write_bytes(canonical_json(payload))
        temporary.replace(progress_path)

    def apply(key, value, origin):
        nonlocal truncated_rows
        value.validate(key)
        for rid in row_hashes[key]:
            status_counts["classified"] += 1
            truncated_rows += int(value.token_count > config.max_length)
            results[rid] = ReviewSentiment(
                rid,
                "classified",
                value.model_label,
                dict(value.probabilities),
                input_sha256=key,
            )
            diagnostics[rid] = {
                "record_id": rid,
                "input_sha256": key,
                "token_count": value.token_count,
                "truncated": value.token_count > config.max_length,
                "origin": origin,
            }

    try:
        save_progress("running")
        for rid, text in zip(ids, reviews.model_text, strict=True):
            status, reason, prepared = None, None, None
            if text is None or (
                not isinstance(text, str)
                and pd.api.types.is_scalar(text)
                and pd.isna(text)
            ):
                status, reason = "excluded", "empty_text"
            elif not isinstance(text, str):
                status, reason = "failed", "invalid_text"
            elif not text.strip():
                status, reason = "excluded", "empty_text"
            else:
                try:
                    prepared = _prepare(text)
                    if not isinstance(prepared, str):
                        raise TypeError("Preprocessing did not return text")
                    if not prepared.strip():
                        status, reason = "excluded", "empty_preprocessed_text"
                except (TypeError, ValueError):
                    status, reason = "failed", "preprocessing_error"
            key = text_hash(prepared) if isinstance(prepared, str) else None
            if status is not None:
                status_counts[status] += 1
                results[rid] = ReviewSentiment(
                    rid, status, reason=reason, input_sha256=key
                )
                diagnostics[rid] = {
                    "record_id": rid,
                    "input_sha256": key,
                    "token_count": None,
                    "truncated": None,
                    "origin": "preparation",
                    "reason": reason,
                }
                continue
            prepared_by_hash.setdefault(key, prepared)
            row_hashes.setdefault(key, []).append(rid)
        save_progress("running")
        with PredictionCache(cache_path, descriptor) as cache:
            pending = []
            for key in prepared_by_hash:
                value = cache.get(key)
                if value is None:
                    pending.append(key)
                else:
                    apply(key, value, "cache")
                    reused.add(key)
            save_progress("running")
            if pending:
                factory = (
                    RobertuitoClassifier
                    if classifier_factory is None
                    else classifier_factory
                )
                classifier = factory(
                    config, model_dir, local_files_only=local_files_only
                )
                for start in range(0, len(pending), config.batch_size):
                    keys = pending[start : start + config.batch_size]
                    values = classifier.predict([prepared_by_hash[key] for key in keys])
                    if len(values) != len(keys):
                        raise ValueError("Classifier returned an unexpected batch size")
                    entries = list(zip(keys, values, strict=True))
                    cache.put_batch(entries)
                    for key, value in entries:
                        apply(key, value, "computed")
                        computed.add(key)
                    save_progress("running")
        save_progress("completed")
    except Exception as error:
        # Keep failures out of the successful prediction cache. Pending IDs and
        # durable completed results let operators inspect and retry the run.
        save_progress("failed", {"type": type(error).__name__, "message": str(error)})
        raise SentimentRunError(report) from error
    return SentimentResult(
        tuple(results[rid] for rid in ids),
        tuple(diagnostics[rid] for rid in ids),
        report,
    )
