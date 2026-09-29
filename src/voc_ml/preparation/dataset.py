"""Versioned minimal preparation. ML-specific policy does not live in VOC."""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

COLUMNS = [
    "record_id",
    "platform",
    "app_id",
    "country",
    "text",
    "title",
    "model_text",
    "text_hash",
    "rating",
    "record_date",
    "date_basis",
    "date_precision",
    "date_timezone",
    "language",
    "language_basis",
    "relevance",
    "preferred_provider",
    "identity_basis",
]
DEFAULT_RULES = {
    "columns": COLUMNS,
    "blank_text": "exclude_and_report",
    "combine_title_body": False,
    "platforms": ["app_store", "google_play", "x", "youtube"],
    "language_filter": None,
    "relevance_filter": None,
}


@dataclass
class Prepared:
    data: pd.DataFrame
    report: dict


def prepare_dataset(raw, rules=None):
    rules = DEFAULT_RULES | (rules or {})
    if set(rules) - set(DEFAULT_RULES):
        raise ValueError("Unknown preparation rule")
    if rules["columns"] != COLUMNS:
        raise ValueError("workable-v1 requires all 18 columns in schema order")
    if rules["blank_text"] != "exclude_and_report":
        raise ValueError("Unsupported blank-text rule")
    if type(rules["combine_title_body"]) is not bool:
        raise ValueError("Expected boolean title rule")
    if not set(rules["platforms"]) <= set(DEFAULT_RULES["platforms"]):
        raise ValueError("Unknown platform")
    missing = set(COLUMNS) - {"model_text", "text_hash"} - set(raw)
    if missing:
        raise ValueError("Missing columns: " + str(sorted(missing)))
    if raw.record_id.isna().any() or not raw.record_id.is_unique:
        raise ValueError("Invalid record IDs")
    source = raw.copy()
    date_normalizations = 0
    # The seed's exploration date is a YYYY-MM-DD bucket even for timestamp sources.
    # Recover precision from its existing canonical timestamp, never invent midnight.
    basis_fields = {
        "source_updated_at": "updated_at",
        "published_at": "published_at",
        "earliest_observed_edit_family_creation": "published_at",
    }
    for index, row in source.iterrows():
        value = row["record_date"]
        precision = row["date_precision"]
        if pd.isna(value):
            if precision != "unknown":
                raise ValueError("Missing date with known precision")
            continue
        if precision == "second":
            if len(value) == 10:
                field = basis_fields.get(row["date_basis"])
                stamp = row.get(field) if field else None
                if not isinstance(stamp, str) or len(stamp) <= 10:
                    raise ValueError("Second precision requires a source timestamp")
                value = stamp
                date_normalizations += 1
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None and row["date_timezone"] != "unknown":
                raise ValueError("Timestamp timezone does not match provenance")
            source.at[index, "record_date"] = dt.isoformat()
        elif precision == "day":
            if len(value) != 10:
                raise ValueError("Day precision requires YYYY-MM-DD")
            date.fromisoformat(value)
        elif precision != "unknown":
            raise ValueError("Invalid date precision")
    reasons = pd.Series("", index=source.index, dtype="string")
    reasons.loc[~source.platform.isin(rules["platforms"])] = "platform_filter"
    for key, column in [
        ("language_filter", "language"),
        ("relevance_filter", "relevance"),
    ]:
        values = rules[key]
        if values is not None:
            if not isinstance(values, list):
                raise ValueError(key + " must be a list or null")
            reasons.loc[(reasons == "") & ~source[column].isin(values)] = key
    blank = source.text.isna() | source.text.astype("string").str.strip().eq("").fillna(
        True
    )
    reasons.loc[blank] = "blank_text"
    excluded = [
        {"record_id": str(source.loc[i, "record_id"]), "reason": str(r)}
        for i, r in reasons.items()
        if r
    ]
    frame = source.loc[reasons == ""].copy()
    frame["model_text"] = frame.text.astype("string").str.strip()
    if rules["combine_title_body"]:
        title = frame.title.astype("string").fillna("").str.strip()
        frame["model_text"] = title.where(title.eq(""), title + "\n") + frame.model_text
    frame["text_hash"] = frame.model_text.map(
        lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()
    )
    frame = frame[COLUMNS]
    for col in COLUMNS:
        if col != "rating":
            frame[col] = frame[col].astype("string")
    rating = pd.to_numeric(frame.rating, errors="raise")
    if not (rating.dropna().isin([1, 2, 3, 4, 5])).all():
        raise ValueError("Invalid rating")
    frame["rating"] = rating.astype("Int64")
    nullable = {"app_id", "country", "title", "rating", "record_date", "language"}
    for col in set(COLUMNS) - nullable:
        if frame[col].isna().any():
            raise ValueError("Null required field: " + col)
    if not frame.platform.isin(DEFAULT_RULES["platforms"]).all():
        raise ValueError("Invalid platform")
    if not frame.preferred_provider.isin(
        ["play", "apple_rss", "appbot", "x", "youtube"]
    ).all():
        raise ValueError("Invalid provider")
    if not frame.relevance.isin(
        [
            "app_target_match",
            "human_relevant",
            "human_irrelevant",
            "human_uncertain",
            "unreviewed",
        ]
    ).all():
        raise ValueError("Invalid relevance")
    if not frame.date_precision.isin(["day", "second", "unknown"]).all():
        raise ValueError("Invalid precision")
    frame = frame.sort_values("record_id").reset_index(drop=True)
    return Prepared(
        frame,
        {
            "schema_version": "workable-v1",
            "date_normalizations": date_normalizations,
            "rules": rules,
            "input_count": len(raw),
            "row_count": len(frame),
            "excluded": excluded,
            "excluded_count": len(excluded),
            "counts_by_platform": frame.platform.value_counts().to_dict(),
        },
    )
