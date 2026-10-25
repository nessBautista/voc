"""Inspect stored embeddings without producing or publishing them."""

from .contracts import EmbeddingInspection
from .operations import inspect_embeddings

__all__ = ["EmbeddingInspection", "inspect_embeddings"]
