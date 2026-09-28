from dataclasses import dataclass
import pandas as pd

# dataclass generates the initializer, so callers can use Snapshot(data, info).
@dataclass
class Snapshot:
    """Keep a dataset and its identifying metadata together in memory."""

    data: pd.DataFrame  # Rows and columns you explore or process.
    info: dict  # Metadata such as the revision/release ID and row count.
