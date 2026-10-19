"""Lazy CPU SentenceTransformer adapter with pinned model files and bounded diagnostics."""

import platform
from importlib.metadata import version
from pathlib import Path

from voc.embeddings.contracts import encoder_identity

ADAPTER_VERSION = "sentence-transformers-cpu-f32-threads4-v1"


def encoder_descriptor(profile):
    descriptor = {
        "profile_id": profile.profile_id,
        "adapter": ADAPTER_VERSION,
        "device": "cpu",
        "dtype": "<f4",
        "software": {
            name: version(name)
            for name in (
                "sentence-transformers",
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
    encoder_identity(descriptor)
    return descriptor


class SentenceEncoder:
    def __init__(self, profile, model_dir, *, local_files_only=False):
        import torch
        from huggingface_hub import snapshot_download
        from sentence_transformers import SentenceTransformer

        self.profile = profile
        # Resolve the exact revision, then load exclusively from that snapshot.
        snapshot = Path(
            snapshot_download(
                repo_id=profile.model,
                revision=profile.revision,
                cache_dir=str(model_dir),
                local_files_only=local_files_only,
                token=False,
                allow_patterns=[
                    "*.json",
                    "*.safetensors",
                    "*.model",
                    "vocab.txt",
                    "merges.txt",
                ],
            )
        )
        if snapshot.name != profile.revision:
            raise ValueError("Resolved model snapshot differs from the pinned revision")
        self.resolved_revision = snapshot.name
        self.model = SentenceTransformer(
            str(snapshot),
            device="cpu",
            local_files_only=True,
            trust_remote_code=False,
            token=False,
        )
        self.model.float()
        self.model.eval()
        self.model.default_prompt_name = None
        self.model.max_seq_length = profile.max_length
        if self.model.get_embedding_dimension() != profile.dimensions:
            raise ValueError("Model dimensions differ from profile")
        if self.model.max_seq_length != profile.max_length:
            raise ValueError("Model truncation length differs from profile")
        position_limit = getattr(
            self.model[0].auto_model.config, "max_position_embeddings", None
        )
        if position_limit is not None and profile.max_length > position_limit:
            raise ValueError("Profile exceeds the model's position limit")
        # Scope the thread setting to encode calls; do not alter notebook-global settings permanently.
        self.torch = torch

    def encode(self, texts, batch_size):
        prompt = self.profile.prompt or ""
        # Mirror the Transformer module's string preprocessing for token diagnostics.
        effective = [(prompt + text).strip() for text in texts]
        if self.model[0].do_lower_case:
            effective = [text.lower() for text in effective]
        tokens = self.model.tokenizer(
            effective,
            add_special_tokens=True,
            padding=False,
            truncation=False,
            return_length=True,
            verbose=False,
        )["length"]
        previous_threads = self.torch.get_num_threads()
        try:
            self.torch.set_num_threads(4)
            vectors = self.model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=self.profile.normalize,
                prompt=prompt,
                precision="float32",
                convert_to_numpy=True,
                show_progress_bar=False,
                device="cpu",
            )
        finally:
            self.torch.set_num_threads(previous_threads)
        return vectors, [int(n) for n in tokens]
