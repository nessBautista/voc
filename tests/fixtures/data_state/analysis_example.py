"""One synthetic voc-data-state-v2 description shared by library, server and web tests.

Sixty fictional reviews over four platforms and thirteen months, five topics
plus an outlier group, sentiment on every review but two, star ratings on the
store reviews, and generated names on three topics. The numbers are chosen so
that every count, share and ranking the dashboard shows is small enough to
check by hand. Nothing here is real collection or model evidence.
"""

import json

import pandas as pd

from voc.models import Snapshot
from voc.publications.contracts import validate_description
from voc.publications.exporter import describe_dataset

DATASET_RELEASE_ID = "7f1c6a0e-3b7d-4f2e-9a61-2c5d8e4b0a13"
EMBEDDING_RELEASE_ID = "4c9e2d11-8a5f-4b6c-9e07-61f3a2b4c5d6"
ANALYSIS_ID = "0e5b7a92-6d3c-4e1f-8b2a-9c4d5e6f7a80"
PLATFORMS = ("google_play", "app_store", "x", "youtube")
MONTHS = [f"2025-{m:02d}" for m in (9, 10, 11, 12)] + [
    f"2026-{m:02d}" for m in range(1, 10)
]
TOPIC_SPECS = {
    # topic_id: (keywords, label pattern over its ten members)
    0: (["lenta", "tarda", "abrir", "carga", "espera", "lento"], "nnnnnnnnpu"),
    1: (["transferencia", "dinero", "saldo", "envío", "cobro", "pago"], "nnnnnuuupp"),
    2: (["fácil", "rápida", "práctica", "usar", "sencilla", "buena"], "pppppppppu"),
    3: (["tarjeta", "bloqueo", "digital", "física", "activar", "pin"], "nnnnnnuupp"),
    4: (
        ["servicio", "atención", "sucursal", "teléfono", "ayuda", "soporte"],
        "nnnuuuuppp",
    ),
}
LABELS = {"n": "negative", "u": "neutral", "p": "positive"}
EXCLUDED = {"analysis-example-007", "analysis-example-019"}


def analysis_frame():
    """The dataset release as the reader would load it: one row per review."""
    rows = []
    for i in range(60):
        platform = PLATFORMS[0] if i % 2 == 0 else PLATFORMS[1 + (i // 2) % 3]
        if i % 5 == 0:
            platform = PLATFORMS[0]
        month = MONTHS[i % len(MONTHS)]
        rows.append(
            {
                "record_id": f"analysis-example-{i:03d}",
                "platform": platform,
                "record_date": f"{month}-{1 + (i * 7) % 27:02d}T12:00:00Z",
                "model_text": f"Reseña ficticia {i}: la aplicación {'tarda' if i % 3 else 'funciona'}.",
                "rating": None if platform in ("x", "youtube") else 1 + (i * 3) % 5,
            }
        )
    return pd.DataFrame(rows)


def _topic_of(i):
    group = i % 6
    return group if group < 5 else -1


def analysis_description():
    """Return the complete v2 description, validated, ready for build_bundle."""
    frame = analysis_frame()
    info = {
        "source": "s3",
        "stage": "prepared",
        "schema_version": "workable-v1",
        "release_id": DATASET_RELEASE_ID,
        "row_count": len(frame),
        "prepared_at": "2026-10-01T00:00:00Z",
    }
    description = describe_dataset(Snapshot(frame, info))
    dataset_id = description["dataset"]["dataset_id"]
    description["embedding_status"] = {
        "dataset_id": dataset_id,
        "state": "registered",
        "release_id": EMBEDDING_RELEASE_ID,
        "model": "minilm-verbatim-v1",
        "covered_rows": len(frame),
        "verification_evidence_id": None,
    }
    description["provenance"] = [
        *description["provenance"],
        {
            "evidence_id": "embedding-registration",
            "dataset_id": dataset_id,
            "kind": "embedding_registration",
            "evidence_ref": f"embedding-release:{EMBEDDING_RELEASE_ID}",
            "description": "Fictional embedding release for the analysis fixture.",
        },
        {
            "evidence_id": "analysis-candidate",
            "dataset_id": dataset_id,
            "kind": "analysis_candidate",
            "evidence_ref": f"analysis-candidate:{ANALYSIS_ID}",
            "description": "Fictional analysis candidate for the analysis fixture.",
        },
    ]

    members = {t: [] for t in (-1, *TOPIC_SPECS)}
    reviews, assignments, sentiment = [], [], []
    for position, row in frame.iterrows():
        rid = row["record_id"]
        topic = _topic_of(position)
        members[topic].append(rid)
        reviews.append(
            {
                "dataset_id": dataset_id,
                "record_id": rid,
                "platform": row["platform"],
                "record_date": row["record_date"],
                "model_text": row["model_text"],
                "vector_row": int(position),
                "rating": None if pd.isna(row["rating"]) else int(row["rating"]),
            }
        )
        assignments.append(
            {
                "dataset_id": dataset_id,
                "record_id": rid,
                "topic_id": topic,
                "membership_strength": 0.0
                if topic == -1
                else 0.55 + (position % 9) * 0.05,
            }
        )
        if rid in EXCLUDED:
            sentiment.append(
                {
                    "dataset_id": dataset_id,
                    "record_id": rid,
                    "status": "excluded",
                    "model_label": None,
                    "negative": None,
                    "neutral": None,
                    "positive": None,
                    "reason": "blank_text",
                    "input_sha256": None,
                }
            )
            continue
        if topic == -1:
            label = "negative" if position % 3 == 0 else "neutral"
        else:
            pattern = TOPIC_SPECS[topic][1]
            label = LABELS[pattern[members[topic].index(rid)]]
        probabilities = {"negative": 0.1, "neutral": 0.1, "positive": 0.1}
        probabilities[label] = 0.8
        sentiment.append(
            {
                "dataset_id": dataset_id,
                "record_id": rid,
                "status": "classified",
                "model_label": label,
                **probabilities,
                "reason": None,
                "input_sha256": f"{position:064x}",
            }
        )

    topics = [
        {
            "dataset_id": dataset_id,
            "topic_id": -1,
            "name": "Unassigned",
            "description": "Reviews without a group.",
            "keywords_json": "[]",
            "evidence_ids_json": json.dumps(members[-1][:3]),
            "member_count": len(members[-1]),
        }
    ]
    for topic_id, (keywords, _) in TOPIC_SPECS.items():
        topics.append(
            {
                "dataset_id": dataset_id,
                "topic_id": topic_id,
                "name": " / ".join(keywords[:3]),
                "description": "Keywords from c-TF-IDF over the group.",
                "keywords_json": json.dumps(
                    [
                        {"term": k, "weight": round(0.3 - 0.04 * n, 3)}
                        for n, k in enumerate(keywords)
                    ]
                ),
                "evidence_ids_json": json.dumps(members[topic_id][:3]),
                "member_count": len(members[topic_id]),
            }
        )

    interpretations = []
    named = {
        0: (
            "Lentitud al abrir y cargar la aplicación",
            "single_feature",
            "Tiempo de carga de la aplicación",
        ),
        1: ("Problemas con transferencias y saldo", "mixed", None),
        2: ("Valoración positiva general de la aplicación", "unclear", None),
    }
    for topic_id, (name, assessment, feature) in named.items():
        interpretations.append(
            {
                "dataset_id": dataset_id,
                "topic_id": topic_id,
                "status": "completed",
                "name": name,
                "topic_description": f"Descripción ficticia del tema {topic_id} para pruebas.",
                "proposed_feature": feature,
                "assessment": assessment,
                "supporting_review_ids_json": json.dumps(members[topic_id][:2]),
                "requested_model": "google/gemini-2.5-flash",
                "prompt_version": "voc-topic-es-v3",
                "attempt_id": f"a{topic_id}b7c9d0-1e2f-4a3b-8c4d-5e6f7a8b9c0{topic_id}",
                "created_at": "2026-10-02T00:00:00+00:00",
            }
        )

    classified = sum(1 for s in sentiment if s["status"] == "classified")
    description["analysis"] = {
        "analysis": {
            "dataset_id": dataset_id,
            "analysis_id": ANALYSIS_ID,
            "parent_analysis_id": None,
            "embedding_release_id": EMBEDDING_RELEASE_ID,
            "embedding_profile": "minilm-verbatim-v1",
            "selection_kind": "full",
            "selection_size": len(frame),
            "selected_reviews": len(frame),
            "assigned_reviews": len(frame) - len(members[-1]),
            "unassigned_reviews": len(members[-1]),
            "topic_count": len(TOPIC_SPECS),
            "classified": classified,
            "excluded": len(frame) - classified,
            "failed": 0,
            "configuration_json": json.dumps({"topics": {"backend": "bertopic"}}),
            "code_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
            "created_at": "2026-10-02T00:00:00+00:00",
        },
        "reviews": reviews,
        "assignments": assignments,
        "topics": topics,
        "sentiment": sentiment,
        "interpretations": interpretations,
    }
    validate_description(description)
    return description
