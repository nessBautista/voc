"""Summarize an already selected dataset without resolving its source again."""

import pandas as pd

from .contracts import DatasetSummary


def summary(snapshot) -> DatasetSummary:
    """Source/month counts shared by the CLI and dashboard; no review bodies."""
    frame = snapshot.data
    dates = pd.to_datetime(
        frame["record_date"], errors="coerce", utc=True, format="mixed"
    )
    return {
        "info": snapshot.info,
        "platform_counts": frame.platform.value_counts().to_dict(),
        "month_counts": dates.dt.strftime("%Y-%m")
        .value_counts()
        .sort_index()
        .to_dict(),
        "unknown_dates": int(dates.isna().sum()),
    }
