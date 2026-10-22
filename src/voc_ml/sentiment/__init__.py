"""Local whole-review sentiment, independent of topic discovery and consumers."""

from .robertuito import (
    SentimentConfig,
    SentimentResult,
    SentimentRunError,
    classify_reviews,
    load_config,
)

__all__ = [
    "SentimentConfig",
    "SentimentResult",
    "SentimentRunError",
    "classify_reviews",
    "load_config",
]
