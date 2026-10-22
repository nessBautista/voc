"""Topic discovery consumes pinned embeddings and returns shared result records."""

from .bertopic import TopicDiscovery, discover
from .reduction import ReductionResult, reduce_inputs

__all__ = ["ReductionResult", "TopicDiscovery", "discover", "reduce_inputs"]
