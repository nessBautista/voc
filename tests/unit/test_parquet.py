import pandas as pd
from voc.datasets.parquet import prepare_frame, restore_frame

def test_mixed_column_round_trip():
    original = pd.DataFrame({"schema": [1.0, "unified-update-v1", None]})
    physical, encoding = prepare_frame(original)
    columns = [{"name": "schema", "pandas_dtype": "object", "encoding": encoding["schema"]}]
    pd.testing.assert_frame_equal(restore_frame(physical.copy(), columns), original)
