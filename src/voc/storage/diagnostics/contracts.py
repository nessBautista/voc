"""Results of explicit storage diagnostics."""

from typing import Literal, TypedDict


class CheckS3Result(TypedDict):
    status: Literal["passed"]
    key: str
    bucket: str
