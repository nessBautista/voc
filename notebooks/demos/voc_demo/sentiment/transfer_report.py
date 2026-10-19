"""Portable, aggregate-only evidence for the published stores-v3 experiment."""

import json
from pathlib import Path


def stores_v3_report():
    evidence = json.loads(
        Path(__file__).with_name("stores_v3_evaluation.json").read_text()
    )
    original, adapted = evidence["baseline_test"], evidence["test"]
    partitions = evidence["dataset"]["partitions"]
    publication = evidence["publication"]
    labels = evidence["label_order"]
    total = sum(part["rows"] for part in partitions.values())
    return {
        "scope": "Stores only · published v3 experiment · held-out test",
        "model": {
            "repo": publication["repo_id"],
            "version": publication["revision"],
            "commit": publication["commit"],
            "base": evidence["source_model"],
            "base_revision": evidence["source_revision"],
        },
        "method": {
            "title": "Keep the language knowledge; adapt the last block and classifier",
            "intro": "Workflow 04 starts from the original RoBERTuito checkpoint. It learns from App Store and Google Play reviews: 1–2 stars become negative, 3 neutral, and 4–5 positive. The social model is a separate experiment.",
            "frozen": f"The first {evidence['encoder_blocks_total'] - evidence['encoder_blocks_trained']} encoder blocks and token embeddings keep their pretrained weights. Reviews still pass through them; frozen means their weights do not change.",
            "trainable": f"The final {evidence['encoder_blocks_trained']} encoder block and the existing classification head update together. AdamW receives only parameters with requires_grad=True; frozen encoder modules also run without dropout.",
            "head": "The existing sentiment classifier adapts to the review domain. Its output remains negative, neutral or positive.",
            "neutral_title": "Neutral remains difficult",
            "selection": f"Train on {partitions['train']['rows']:,} reviews, select the best checkpoint using validation macro F1 on {partitions['validation']['rows']:,}, then report test performance on {partitions['test']['rows']:,}. Equivalent texts stay in one partition.",
            "interpretation": "Macro F1 gives negative, neutral and positive equal weight. Accuracy counts correct predictions across all reviews; weighted F1 gives larger classes more influence. Improvements here describe this test set, not guaranteed performance on future reviews.",
        },
        "architecture": {
            "total_blocks": evidence["encoder_blocks_total"],
            "frozen_blocks": evidence["encoder_blocks_total"]
            - evidence["encoder_blocks_trained"],
            "trained_blocks": evidence["encoder_blocks_trained"],
        },
        "training": {
            key: evidence[key]
            for key in (
                "epochs",
                "best_epoch",
                "batch_size",
                "learning_rate",
                "seed",
                "device",
                "trainable_parameters",
                "total_parameters",
                "trainable_percent",
            )
        },
        "data": {
            "rows": total,
            "partitions": partitions,
            "language_audit": evidence["dataset"]["language_audit"],
        },
        "comparison": [
            {
                "key": key,
                "label": label,
                "original": original[group][metric] if group else original[metric],
                "v3": adapted[group][metric] if group else adapted[metric],
            }
            for key, label, group, metric in (
                ("macro_f1", "Macro F1", "macro avg", "f1-score"),
                ("accuracy", "Accuracy", None, "accuracy"),
                ("weighted_f1", "Weighted F1", "weighted avg", "f1-score"),
            )
        ],
        "classes": [
            {
                "label": label,
                "original_f1": original[label]["f1-score"],
                "v3_f1": adapted[label]["f1-score"],
                "v3_recall": adapted[label]["recall"],
                "support": int(adapted[label]["support"]),
            }
            for label in labels
        ],
        "labels": labels,
        "confusion_matrix": evidence["confusion_matrix"],
        "limits": "Star-derived labels approximate sentiment. No independent external evaluation or aspect-level evaluation has been completed. The language audit sampled reviews and retained every input row.",
    }
