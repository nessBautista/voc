"""Run independent store/social experiments from the original checkpoint."""

import csv
import gc
import hashlib
import json
from io import BytesIO, StringIO

import pandas as pd

from .adaptation import adapt, prepare_splits

PLATFORMS = {"rated": {"app_store", "google_play"}, "social": {"x", "youtube"}}


def load_experiment_data(paths):
    """Validate both inputs before any model download or training."""
    if set(paths) != set(PLATFORMS):
        raise ValueError("Selecciona los dos datasets: rated y social.")
    datasets = {}
    for name, path in paths.items():
        if not path.is_file() and path.parent.is_dir():
            matches = [
                candidate
                for candidate in path.parent.iterdir()
                if candidate.name.casefold() == path.name.casefold()
                and candidate.is_file()
            ]
            if len(matches) == 1:
                path = matches[0]
        if not path.is_file():
            raise ValueError(f"No se encontró el CSV {name}: {path}")
        if path.suffix.lower() != ".csv":
            raise ValueError(f"Exporta {path.name} como CSV antes de entrenar.")
        content = path.read_bytes()
        rows = csv.reader(StringIO(content.decode("utf-8-sig")))
        first, second = next(rows, []), next(rows, [])
        required = {"record_id", "text", "label", "platform"}
        # Numbers may insert the table name as a single field before headers.
        title_row = len(first) == 1 and required <= set(second)
        frame = pd.read_csv(
            BytesIO(content),
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
            skiprows=1 if title_row else 0,
        )
        if "label" in frame:
            frame["label"] = (
                frame.label.str.strip()
                .str.casefold()
                .replace(
                    {
                        "negativo": "negative",
                        "neutral": "neutral",
                        "positivo": "positive",
                    }
                )
            )
        try:
            splits = prepare_splits(frame)
        except ValueError as error:
            raise ValueError(f"Dataset {name}: {error}") from error
        if not set(frame.platform.str.strip()) <= PLATFORMS[name]:
            raise ValueError(
                f"El dataset {name} contiene plataformas que no le corresponden."
            )
        datasets[name] = {
            "splits": splits,
            "source_csv": str(path.resolve()),
            "source_sha256": hashlib.sha256(content).hexdigest(),
        }
    rated_ids = set(pd.concat(datasets["rated"]["splits"].values()).record_id)
    social_ids = set(pd.concat(datasets["social"]["splits"].values()).record_id)
    if rated_ids & social_ids:
        raise ValueError("Los datasets comparten identificadores de reseña.")
    return datasets


def train_experiments(
    datasets, output, cache, *, epochs=3, batch_size=8, on_progress=None
):
    """Train sequentially and release weights between independent runs."""
    if set(datasets) != set(PLATFORMS):
        raise ValueError("Se requieren ambos datasets validados.")
    output.mkdir(parents=True, exist_ok=False)
    results = {}
    for name in ("rated", "social"):
        if on_progress is not None:
            on_progress(name)
        run_output = output / name
        # adapt() reloads the original model, never the preceding experiment.
        tokenizer, model, metrics = adapt(
            datasets[name]["splits"],
            run_output,
            cache,
            epochs=epochs,
            batch_size=batch_size,
        )
        provenance = {
            key: datasets[name][key] for key in ("source_csv", "source_sha256")
        }
        (run_output / "dataset.json").write_text(
            json.dumps(provenance, indent=2), encoding="utf-8"
        )
        results[name] = {
            "output": str(run_output.resolve()),
            "metrics": metrics,
            **provenance,
        }
        del tokenizer, model
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        # Preserve the first completion even if the second experiment fails.
        (output / "experiments.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return results


def load_trained_model(output):
    """Load only the selected experiment for inference."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_path = output / "model"
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_path, trust_remote_code=False, use_safetensors=True
    ).eval()
    return tokenizer, model
