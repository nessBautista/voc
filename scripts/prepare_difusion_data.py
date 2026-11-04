"""Copy the agreed historical inputs into this checkout, verifying each file.

No model execution, cloud access, credential copying or metadata DB migration.
Existing files are accepted only when identical; this script never replaces them.
"""

import argparse
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

DATASET = "b7a61a8b-27d8-46a7-b2ce-67d97268b741"
EMBEDDINGS = "23243c6e-7787-463c-ac3b-bfe950060d6e"
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="Read-only demo checkout"
    )
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if source == ROOT or source.is_relative_to(ROOT) or ROOT.is_relative_to(source):
        raise ValueError("Source and destination must be independent checkouts")
    target = ROOT / "data"
    if target.is_symlink() or target.resolve() != target:
        raise ValueError("The runtime data directory must stay in this checkout")
    entries = []

    def copy_file(src, dst, expected=None):
        if not src.resolve().is_relative_to(source):
            raise ValueError(f"Source escapes its checkout: {src}")
        if dst.is_symlink() or not dst.resolve().is_relative_to(target):
            raise ValueError(f"Destination escapes runtime: {dst}")
        before = digest(src)
        if expected is not None and before != expected:
            raise ValueError(f"Source differs from its manifest: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if digest(dst) != before:
                raise ValueError(
                    f"Existing destination differs; refusing replacement: {dst}"
                )
        else:
            # Exclusive file creation also protects against a concurrently created path.
            with src.open("rb") as reader, dst.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
        if digest(dst) != before or digest(src) != before:
            raise ValueError(f"Copy or source changed during verification: {src}")
        if os.path.samefile(src, dst):
            raise ValueError("Copied file shares its source inode")
        entries.append(
            {
                "source": str(src.relative_to(source)),
                "destination": str(dst.relative_to(target)),
                "sha256": before,
                "bytes": dst.stat().st_size,
            }
        )

    manifests = sorted((source / "data/cache/datasets").glob(f"*/{DATASET}.json"))
    if not manifests:
        raise FileNotFoundError("Historical dataset cache is missing")
    cached = manifests[0]
    wrapper = json.loads(cached.read_text())
    manifest = json.loads(wrapper["text"])
    if manifest["release_id"] != DATASET:
        raise ValueError("Unexpected dataset release")
    metadata = manifest["workable"]
    candidates = [
        p for p in cached.parent.glob("*.parquet") if digest(p) == metadata["sha256"]
    ]
    if len(candidates) != 1:
        raise ValueError("Expected one prepared Parquet matching the dataset manifest")
    print("Copying prepared corpus...", flush=True)
    copy_file(
        candidates[0], target / "inputs/corpus/dataset.parquet", metadata["sha256"]
    )
    copy_file(cached, target / "inputs/corpus/cached-manifest.json")
    extracted = target / "inputs/corpus/manifest.json"
    if extracted.exists() and extracted.read_text() != wrapper["text"]:
        raise ValueError("Existing extracted manifest differs")
    if not extracted.exists():
        with extracted.open("x") as stream:
            stream.write(wrapper["text"])

    releases = list((source / "data/cache/embeddings/releases").glob(f"*/{EMBEDDINGS}"))
    if len(releases) != 1:
        raise ValueError("Expected one full embedding release")
    release = releases[0]
    bundle = json.loads((release / "manifest.json").read_text())
    if bundle["dataset"]["release_id"] != DATASET:
        raise ValueError("Embedding dataset identity differs")
    print("Copying full embedding release...", flush=True)
    copy_file(release / "manifest.json", target / "inputs/embeddings/manifest.json")
    for key, name in [("reviews", "reviews.parquet"), ("vectors", "vectors.npy")]:
        copy_file(
            release / name,
            target / "inputs/embeddings" / name,
            bundle["artifacts"][key]["sha256"],
        )

    print("Copying saved 1,000-row demo artifacts...", flush=True)
    demo = source / "data/prototypeV0/embeddings"
    for file in sorted(demo.iterdir()):
        if file.suffix in (".json", ".parquet", ".npz", ".npy"):
            copy_file(file, target / "prototypeV0/embeddings" / file.name)

    for directory in [
        "sentence-transformers/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2",
        "sentiment/models--pysentimiento--robertuito-sentiment-analysis",
    ]:
        print(f"Copying pinned model cache: {directory}...", flush=True)
        tree = source / "data/models" / directory
        if not tree.is_dir():
            raise FileNotFoundError(tree)
        for file in sorted(tree.rglob("*")):
            dst = target / "models" / directory / file.relative_to(tree)
            if file.is_symlink():
                link = os.readlink(file)
                if Path(link).is_absolute() or not file.resolve(
                    strict=True
                ).is_relative_to(tree):
                    raise ValueError(f"Unsafe model cache symlink: {file}")
                if (
                    not dst.parent.joinpath(link)
                    .resolve()
                    .is_relative_to(target / "models")
                ):
                    raise ValueError("Model link would escape copied cache")
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.is_symlink():
                    if os.readlink(dst) != link:
                        raise ValueError(f"Existing model link differs: {dst}")
                elif dst.exists():
                    raise ValueError(
                        f"Existing model path is not the expected link: {dst}"
                    )
                else:
                    dst.symlink_to(link)
                entries.append(
                    {
                        "source": str(file.relative_to(source)),
                        "destination": str(dst.relative_to(target)),
                        "link": link,
                    }
                )
            elif file.is_file():
                copy_file(file, dst)
    for file in (target / "models").rglob("*"):
        if file.is_symlink() and not file.resolve(strict=True).is_relative_to(
            target / "models"
        ):
            raise ValueError("Copied model link is not local")
    receipt = {
        "schema_version": "voc-difusion-local-copy-v1",
        "completed_at": datetime.now(UTC).isoformat(),
        "source": str(source),
        "dataset_release_id": DATASET,
        "embedding_release_id": EMBEDDINGS,
        "files": entries,
        "extracted_manifest_sha256": digest(extracted),
        "copied_bytes": sum(item.get("bytes", 0) for item in entries),
        "model_requests": 0,
        "fits": 0,
        "excluded": [
            "credentials",
            "locks",
            "SQLite sessions",
            "ZenML/MLflow state",
            "old environments",
        ],
    }
    output = target / "setup/copy-receipt.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(
        json.dumps(
            {
                "receipt": str(output),
                "entries": len(entries),
                "copied_bytes": receipt["copied_bytes"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
