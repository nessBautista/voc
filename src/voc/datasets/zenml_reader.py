import os
import pandas as pd
from voc.paths import data_root
class ZenMLReader:
    """Optional adapter. ZenML handles materialization and local/S3 artifact stores."""

    def read(self, artifact_id):
        os.environ.setdefault("ZENML_CONFIG_PATH", str(data_root() / "zenml-client"))
        from zenml.client import Client

        data = Client().get_artifact_version(artifact_id).load()
        if not isinstance(data, pd.DataFrame):
            raise TypeError("Expected dataframe artifact")
        return data
