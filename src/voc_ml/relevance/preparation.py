"""Recover saved X labels without executing notebook code; draft a new YT audit."""

import ast
import json
import re
from pathlib import Path

import pandas as pd

from voc.analysis.local_inputs import load_local_config, read_local_inputs
from voc.datasets.manifest import file_hash

SOURCE_SHA256 = "c8127cbf76b0bf2013b1373cc0a4df021f3572c554dbc992d77e884770405666"
LABELS = {"relevant", "irrelevant", "uncertain"}


def recover_x(notebook, social):
    """Indices belong to the original ordered social subset, checked against text."""
    path = Path(notebook)
    if file_hash(path) != SOURCE_SHA256:
        raise ValueError("Notebook differs from the inspected historical source")
    labels, texts = {}, {}
    for cell in json.loads(path.read_bytes())["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        if source.startswith("etiquetas_lote_x_"):
            for node in ast.parse(source).body:
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id.startswith("etiquetas_lote_x_")
                ):
                    for index, label in ast.literal_eval(node.value).items():
                        if (
                            index in labels
                            or type(index) is not int
                            or label not in LABELS
                        ):
                            raise ValueError("Invalid or repeated saved annotation")
                        labels[index] = label
        if source.startswith(("lote_x =", "pendientes_x =")):
            output = "\n".join(
                "".join(o.get("text", o.get("data", {}).get("text/plain", [])))
                for o in cell.get("outputs", [])
            )
            for index, text in re.findall(
                r"^\[(\d+)\]\n(.*?)\n-{100}", output, re.MULTILINE | re.DOTALL
            ):
                texts[int(index)] = text.strip()
    if len(labels) != 100 or labels.keys() != texts.keys():
        raise ValueError("Incomplete saved labels/texts")
    rows = []
    for index, label in labels.items():
        row = social.iloc[index]
        if row.platform != "x" or row.text.strip() != texts[index]:
            raise ValueError(
                "Historical annotation index/text does not match pinned corpus"
            )
        rows.append(
            {
                "record_id": row.record_id,
                "platform": "x",
                "text": row.text,
                "label": label,
                "annotation_origin": "recovered-notebook",
                "annotator": "historical-notebook-author-unverified",
                "source_sha256": SOURCE_SHA256,
                "source_index": index,
            }
        )
    return pd.DataFrame(rows)


def prepare_annotations(local_inputs, notebook, output, *, youtube_size=105):
    snapshot, bundle = read_local_inputs(load_local_config(local_inputs))
    # Reconstruct exactly the merge and social subset order used in the source.
    ordered = bundle.reviews[["record_id"]].merge(
        snapshot.data, on="record_id", validate="one_to_one", sort=False
    )
    social = ordered.loc[ordered.platform.isin(["x", "youtube"])].reset_index(drop=True)
    recovered = recover_x(notebook, social)
    youtube = social.loc[
        social.platform.eq("youtube"), ["record_id", "platform", "text"]
    ]
    youtube = youtube.sort_values("record_id").drop_duplicates("text")
    if not 1 <= youtube_size <= len(youtube):
        raise ValueError("Invalid YouTube annotation sample size")
    draft = (
        youtube.sample(n=youtube_size, random_state=42).sort_values("record_id").copy()
    )
    draft["label"] = ""
    draft["annotation_origin"] = "new-annotation-required"
    draft["annotator"] = ""
    draft["source_sha256"] = ""
    draft["source_index"] = ""
    result = pd.concat([recovered, draft], ignore_index=True)
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    result.to_csv(target / "annotations.csv", index=False)
    metadata = {
        "schema_version": "voc-relevance-reconstruction-v1",
        "dataset_release_id": snapshot.info["release_id"],
        "embedding_release_id": bundle.info["embedding_release_id"],
        "source_notebook_sha256": SOURCE_SHA256,
        "recovered_x": len(recovered),
        "recovered_labels": recovered.label.value_counts().to_dict(),
        "pending_youtube": len(draft),
        "youtube_policy": "new-sorted-record-id-distinct-text-sample-seed42-v1",
        "historical_youtube_labels_recovered": False,
        "annotations_sha256": file_hash(target / "annotations.csv"),
        "training_ready": False,
        "fits": 0,
    }
    (target / "preparation.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def import_review(annotations, reviewed_csv, output):
    """Merge explicit human decisions; proposed labels never become targets implicitly."""
    original = pd.read_csv(annotations, keep_default_na=False, dtype=str)
    review = pd.read_csv(reviewed_csv, keep_default_na=False, dtype=str)
    required = {
        "record_id",
        "platform",
        "text",
        "human_label",
        "human_reviewer",
        "review_status",
    }
    if (
        not required <= set(review)
        or not review.record_id.is_unique
        or not review.human_label.isin(LABELS).all()
        or not review.review_status.eq("reviewed").all()
        or review.human_reviewer.str.strip().eq("").any()
    ):
        raise ValueError(
            "Every proposal needs an explicit reviewed human label and reviewer"
        )
    youtube = original.loc[original.platform.eq("youtube")].set_index("record_id")
    if set(review.record_id) != set(youtube.index):
        raise ValueError("Human review must cover exactly the prepared YouTube IDs")
    for row in review.itertuples():
        source = youtube.loc[row.record_id]
        if row.platform != source.platform or row.text != source.text:
            raise ValueError("Reviewed text/platform differs from prepared annotation")
        mask = original.record_id.eq(row.record_id)
        original.loc[mask, "label"] = row.human_label
        original.loc[mask, "annotator"] = row.human_reviewer
        original.loc[mask, "annotation_origin"] = "human-reviewed-assisted-proposal"
        original.loc[mask, "source_sha256"] = file_hash(reviewed_csv)
    with Path(output).open("x") as stream:
        original.to_csv(stream, index=False)
    return {
        "rows": len(original),
        "human_reviewed_youtube": len(review),
        "annotations_sha256": file_hash(output),
        "fits": 0,
    }
