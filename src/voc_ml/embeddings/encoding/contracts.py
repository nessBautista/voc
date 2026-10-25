"""The existing CPU adapter identity used by cache and encoding reports."""

from typing import Literal, TypedDict

ADAPTER_VERSION = "sentence-transformers-cpu-f32-threads4-v1"


class EncoderDescriptor(TypedDict):
    profile_id: str
    adapter: str
    device: Literal["cpu"]
    dtype: Literal["<f4"]
    software: dict[str, str]
