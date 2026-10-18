"""Synthetic inputs for the data-state tutorial; never real collection evidence.

These dictionaries are test inputs, not the publication or manifest format.
Dates use ISO 8601 UTC strings; None means the date is unknown.
"""


def example_a():
    """Return twelve fictional records and their fictional collection metadata."""
    # Stable fixture identities let tests refer to the same dataset and evidence.
    dataset_id = "fixture-data-state-a"
    collection_evidence_id = "fixture-a-google-play-collection"

    # Review dates describe the records, separately from when they were collected.
    records = [
        # July: three Google Play records and one App Store record.
        {
            "record_id": "fixture-a-play-01",
            "platform": "google_play",
            "record_date": "2026-07-03T12:00:00Z",
        },
        {
            "record_id": "fixture-a-play-02",
            "platform": "google_play",
            "record_date": "2026-07-12T12:00:00Z",
        },
        {
            "record_id": "fixture-a-play-03",
            "platform": "google_play",
            "record_date": "2026-07-25T12:00:00Z",
        },
        {
            "record_id": "fixture-a-apple-01",
            "platform": "app_store",
            "record_date": "2026-07-18T12:00:00Z",
        },
        # August: two records from each store.
        {
            "record_id": "fixture-a-play-04",
            "platform": "google_play",
            "record_date": "2026-08-05T12:00:00Z",
        },
        {
            "record_id": "fixture-a-play-05",
            "platform": "google_play",
            "record_date": "2026-08-22T12:00:00Z",
        },
        {
            "record_id": "fixture-a-apple-02",
            "platform": "app_store",
            "record_date": "2026-08-09T12:00:00Z",
        },
        {
            "record_id": "fixture-a-apple-03",
            "platform": "app_store",
            "record_date": "2026-08-27T12:00:00Z",
        },
        # September: two Google Play records and one App Store record.
        {
            "record_id": "fixture-a-play-06",
            "platform": "google_play",
            "record_date": "2026-09-04T12:00:00Z",
        },
        {
            "record_id": "fixture-a-play-07",
            "platform": "google_play",
            "record_date": "2026-09-21T12:00:00Z",
        },
        {
            "record_id": "fixture-a-apple-04",
            "platform": "app_store",
            "record_date": "2026-09-16T12:00:00Z",
        },
        # This record contributes to the total, but not to any calendar month.
        {
            "record_id": "fixture-a-play-08",
            "platform": "google_play",
            "record_date": None,
        },
    ]

    # Return fresh objects on every call so one test cannot change another's data.
    # Tests will calculate the counts from records instead of trusting saved totals.
    return {
        "dataset_id": dataset_id,
        "source_kind": "fixture",
        "source_version": "example-a-v1",
        "records": records,
        "collection": {
            "google_play": {
                "collection_state": "verified",
                "latest_collection_at": "2026-10-15T12:00:00Z",
                "collection_evidence_id": collection_evidence_id,
            },
            "app_store": {
                "collection_state": "unknown",
                "latest_collection_at": None,
                "collection_evidence_id": None,
            },
        },
        # Verified here means supported within this fictional test scenario only.
        "provenance": [
            {
                "evidence_id": collection_evidence_id,
                "dataset_id": dataset_id,
                "kind": "collection",
                "evidence_ref": "fixture:example-a/google-play-collection",
                "description": (
                    "Fictional Google Play collection on 2026-10-15, "
                    "declared for tutorial tests; not a real collection."
                ),
            },
        ],
    }


def example_b():
    """Return example A plus one App Store record, as a different fixture dataset."""
    # Start with fresh records so changes to B leave example A untouched.
    example = example_a()
    example["dataset_id"] = "fixture-data-state-b"
    example["source_version"] = "example-b-v1"

    # Add the thirteenth record to September without filling the unknown date.
    example["records"].append(
        {
            "record_id": "fixture-b-apple-05",
            "platform": "app_store",
            "record_date": "2026-09-28T12:00:00Z",
        }
    )

    # Give the new dataset its own fictional evidence association.
    evidence_id = "fixture-b-google-play-collection"
    example["collection"]["google_play"]["collection_evidence_id"] = evidence_id
    example["provenance"][0].update(
        {
            "evidence_id": evidence_id,
            "dataset_id": example["dataset_id"],
            "evidence_ref": "fixture:example-b/google-play-collection",
        }
    )
    return example
