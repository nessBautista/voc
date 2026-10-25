"""Encode verified features with concrete local cache and CPU model adapters."""

from .contracts import EncoderDescriptor
from .operations import encode_features

__all__ = ["EncoderDescriptor", "encode_features"]
