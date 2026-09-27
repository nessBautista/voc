"""Lossless scalar encoding for raw object columns that Arrow cannot represent."""

import json
import math
from numbers import Integral, Real

import pandas as pd
import pyarrow as pa

ENCODING = "json-scalar-v1"


def encode_scalar(value):
    if value is None:
        pair = ["null", None]
    elif value is pd.NA:
        pair = ["pd.NA", None]
    elif isinstance(value, bool):
        pair = ["bool", value]
    elif isinstance(value, str):
        pair = ["str", value]
    elif isinstance(value, Integral):
        pair = ["int", int(value)]
    elif isinstance(value, Real):
        number = float(value)
        pair = ["float", number if math.isfinite(number) else str(number)]
    else:
        raise TypeError(
            "Mixed raw columns support only scalar strings/numbers/bools/nulls"
        )
    return json.dumps(pair, separators=(",", ":"), allow_nan=False)


def decode_scalar(value):
    pair = json.loads(value)
    if not isinstance(pair, list) or len(pair) != 2:
        raise ValueError("Invalid encoded raw scalar")
    tag, value = pair
    if tag == "null" and value is None:
        return None
    if tag == "pd.NA" and value is None:
        return pd.NA
    if tag == "str" and isinstance(value, str):
        return value
    if tag == "bool" and type(value) is bool:
        return value
    if tag == "int" and type(value) is int:
        return value
    if tag == "float" and (
        type(value) is float
        or (isinstance(value, str) and value in ("nan", "inf", "-inf"))
    ):
        return float(value)
    raise ValueError("Invalid encoded raw scalar type")


def prepare_frame(frame):
    """Only encode object columns for which Arrow inference fails."""
    physical = frame.copy(deep=False)
    encodings = {}
    for column in frame:
        try:
            pa.Array.from_pandas(frame[column])
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            if frame[column].dtype != object:
                raise
            physical[column] = frame[column].map(encode_scalar)
            encodings[column] = ENCODING
    return physical, encodings


def restore_frame(frame, columns):
    """Restore logical values according to explicit manifest column metadata."""
    for column in columns:
        encoding = column.get("encoding")
        if encoding is None:
            continue
        if encoding != ENCODING or column["pandas_dtype"] != "object":
            raise ValueError("Unsupported raw column encoding")
        name = column["name"]
        frame[name] = pd.Series(
            [decode_scalar(value) for value in frame[name]],
            index=frame.index,
            dtype=object,
        )
    return frame
