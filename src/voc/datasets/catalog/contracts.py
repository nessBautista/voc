"""Required catalog reference fields; completed-run validation belongs to ML."""


def validate_publication(info):
    for key in (
        "run_id",
        "artifact_id",
        "raw_revision_id",
        "schema_version",
        "row_count",
    ):
        if key not in info:
            raise ValueError("Missing publication field: " + key)
