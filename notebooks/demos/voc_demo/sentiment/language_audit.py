"""Deterministic language screening for an explicit sample or a complete CSV."""

import hashlib
import re
from functools import lru_cache
from io import BytesIO

import pandas as pd


@lru_cache(maxsize=100_000)
def detect_language(text):
    from langdetect import DetectorFactory, detect_langs
    from langdetect.lang_detect_exception import LangDetectException

    DetectorFactory.seed = 0
    try:
        result = detect_langs(text)[0]
        return result.lang, float(result.prob)
    except LangDetectException:
        return "und", 0.0


def audit_languages(
    content, *, full=False, sample_size=500, threshold=0.8, min_letters=20
):
    """Inspect raw text before sentiment preprocessing; never infer sentiment labels."""
    if not 0 < threshold <= 1 or min_letters < 1 or sample_size < 1:
        raise ValueError(
            "Use a confidence threshold in (0, 1] and positive sample/letter counts."
        )
    frame = pd.read_csv(
        BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig"
    )
    if not {"record_id", "text"} <= set(frame):
        raise ValueError("The CSV must include record_id and text.")
    if not frame.record_id.is_unique:
        raise ValueError("Review IDs must be unique.")
    sample = (
        frame if full else frame.sample(n=min(sample_size, len(frame)), random_state=42)
    )
    rows = []
    for row in sample.itertuples(index=False):
        text = re.sub(r"https?://\S+|@\w+", " ", str(row.text))
        if sum(character.isalpha() for character in text) < min_letters:
            lang, score, status = "und", 0.0, "short_text"
        else:
            lang, score = detect_language(text)
            status = (
                "uncertain"
                if score < threshold or lang == "und"
                else "spanish"
                if lang == "es"
                else "other_language"
            )
        rows.append(
            {
                "record_id": row.record_id,
                "text": row.text,
                "detected_language": lang,
                "detector_score": score,
                "status": status,
            }
        )
    audit = pd.DataFrame(
        rows,
        columns=["record_id", "text", "detected_language", "detector_score", "status"],
    )
    metadata = {
        "detector": "langdetect",
        "version": "1.0.9",
        "seed": 0,
        "sampling_seed": 42,
        "threshold": threshold,
        "min_letters": min_letters,
        "full_audit": full,
        "source_rows": len(frame),
        "audited_rows": len(audit),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "audited_ids_sha256": hashlib.sha256(
            "\n".join(audit.record_id).encode()
        ).hexdigest(),
        "status_counts": audit.status.value_counts().to_dict(),
    }
    return frame, audit, metadata


def select_language_rows(frame, audit, metadata, spanish_only=False):
    """Filtering is explicit and requires auditing every input row first."""
    if spanish_only and not metadata["full_audit"]:
        raise ValueError(
            "Choose a full audit before filtering to Spanish-only reviews."
        )
    selected = frame.copy()
    if spanish_only:
        selected = frame.loc[
            frame.record_id.isin(audit.loc[audit.status.eq("spanish"), "record_id"])
        ].copy()
    info = {
        **metadata,
        "policy": "spanish_only" if spanish_only else "keep_all",
        "retained_rows": len(selected),
        "excluded_rows": len(frame) - len(selected),
    }
    return selected.to_csv(index=False).encode(), info
