import pandas as pd
import pytest
from conftest import row

from voc.datasets import get_dataset, publish, resolve


def info(run, artifact):
    return {
        "run_id": run,
        "artifact_id": artifact,
        "raw_revision_id": "raw1",
        "schema_version": "workable-v1",
        "row_count": 1,
    }


def test_publication_history_idempotency_and_reader(tmp_path):
    p = tmp_path / "catalog.db"
    with pytest.raises(LookupError):
        resolve(path=p)
    a = info("run1", "a1")
    publish(a, path=p)
    publish(a, path=p)
    publish(info("run2", "a2"), path=p)
    assert resolve(path=p)["artifact_id"] == "a2"
    assert resolve("a1", path=p) == a

    class Reader:
        def read(self, artifact_id):
            assert artifact_id == "a1"
            return pd.DataFrame([row(1)])

    assert len(get_dataset("a1", reader=Reader(), path=p).data) == 1
    with pytest.raises(ValueError):
        publish(info("run1", "other"), path=p)
