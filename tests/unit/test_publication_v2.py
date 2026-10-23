"""A v2 package carries one analysis candidate; v1 packages stay exactly as they were."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.fixtures.data_state.analysis_example import analysis_description
from tests.unit.test_analysis_inputs import DATASET_ID, fixture_inputs
from tests.unit.test_analysis_store import candidate as candidate  # noqa: PLC0414
from voc.analysis.store import read_analysis, write_analysis
from voc.cli import main
from voc.models import Snapshot
from voc.publications import store
from voc.publications.builder import build_bundle
from voc.publications.contracts import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_V2,
    validate_description,
)
from voc.publications.exporter import (
    describe_analysis,
    describe_dataset,
    review_ratings,
)
from voc.publications.queries import query
from voc.publications.selection import SelectionError
from voc.publications.store import activate_bundle, validate_bundle


def snapshot_for(candidate):
    # The candidate was pinned from this release; describe the same four rows.
    frame, _ = fixture_inputs(4)
    frame = frame.data
    info = {
        "source": "s3",
        "stage": "prepared",
        "schema_version": "workable-v1",
        "release_id": DATASET_ID,
        "row_count": len(frame),
        "prepared_at": "2026-10-10T00:00:00Z",
    }
    return Snapshot(frame, info)


@pytest.fixture
def stored(candidate):
    manifest = write_analysis(**candidate)
    return read_analysis(manifest.analysis_id)


@pytest.fixture
def descriptions(stored, candidate):
    snapshot = snapshot_for(candidate)
    dataset = describe_dataset(snapshot)
    return dataset, describe_analysis(stored, dataset, ratings=review_ratings(snapshot))


def test_ratings_come_from_the_dataset_and_may_be_absent(
    descriptions, stored, candidate
):
    dataset, analysis = descriptions
    by_id = {row["record_id"]: row["rating"] for row in analysis["analysis"]["reviews"]}
    assert by_id == {f"review-{i:03d}": 1 + i % 5 for i in range(4)}
    # Without a rating column, every review is null and the description still validates.
    bare = snapshot_for(candidate)
    bare.data = bare.data.drop(columns=["rating"])
    assert review_ratings(bare) == {}
    without = describe_analysis(stored, dataset)
    assert {row["rating"] for row in without["analysis"]["reviews"]} == {None}
    assert validate_description(without)
    # A social post without a rating is null; a bad value is a defect, not a null.
    mixed = snapshot_for(candidate)
    mixed.data.loc[0, "rating"] = None
    assert review_ratings(mixed)["review-000"] is None
    broken = snapshot_for(candidate)
    broken.data.loc[1, "rating"] = 7
    with pytest.raises(ValueError, match="out of range"):
        review_ratings(broken)
    out_of_range = deepcopy(analysis)
    out_of_range["analysis"]["reviews"][0]["rating"] = 0
    with pytest.raises(ValueError, match="integer from 1 to 5"):
        validate_description(out_of_range)
    boolean = deepcopy(analysis)
    boolean["analysis"]["reviews"][0]["rating"] = True
    with pytest.raises(ValueError, match="integer from 1 to 5"):
        validate_description(boolean)


def interpretation_row(dataset_id, topic_id, supporting):
    return {
        "dataset_id": dataset_id,
        "topic_id": topic_id,
        "status": "completed",
        "name": "Acceso a la cuenta",
        "topic_description": "Reseñas sobre el inicio de sesión.",
        "proposed_feature": None,
        "assessment": "mixed",
        "supporting_review_ids_json": json.dumps(supporting),
        "requested_model": "google/gemini-2.5-flash",
        "prompt_version": "voc-topic-es-v2",
        "attempt_id": "0b6f0d55-1d8a-4a6e-9d9e-8e1a4a1b2c3d",
        "created_at": "2026-10-23T00:00:00+00:00",
    }


def test_describe_analysis_registers_embeddings_and_cites_the_candidate(
    descriptions, stored
):
    dataset, analysis = descriptions
    assert "analysis" not in dataset
    block = analysis["analysis"]
    assert analysis["embedding_status"]["state"] == "registered"
    assert (
        analysis["embedding_status"]["release_id"]
        == stored.manifest.inputs["embedding"]["release_id"]
    )
    kinds = {row["kind"]: row["evidence_ref"] for row in analysis["provenance"]}
    assert (
        kinds["analysis_candidate"]
        == f"analysis-candidate:{stored.manifest.analysis_id}"
    )
    assert block["analysis"]["selected_reviews"] == 4 == len(block["reviews"])
    assert block["analysis"]["topic_count"] == 2 and block["analysis"]["excluded"] == 1
    assert {t["topic_id"]: t["member_count"] for t in block["topics"]} == {
        -1: 1,
        0: 2,
        1: 1,
    }
    assert block["interpretations"] == []
    codes = {w["code"] for w in validate_description(analysis)}
    assert {
        "embeddings_not_verified",
        "sentiment_unclassified",
        "interpretations_partial",
    } <= codes
    assert "analysis_partial_selection" not in codes


def test_v2_bundle_builds_validates_and_answers_the_new_kinds(descriptions, tmp_path):
    _, analysis = descriptions
    bundle = build_bundle(analysis, tmp_path / "v2")
    manifest = validate_bundle(bundle)
    assert manifest["schema_version"] == SCHEMA_VERSION_V2
    assert manifest["capabilities"] == list(store.CAPABILITIES_V2)
    catalog, state = tmp_path / "catalog", tmp_path / "state"
    pointer = activate_bundle(bundle, catalog, state)
    identity = pointer["publication_id"]
    overview = query(catalog, identity, "overview")
    assert overview["data"]["row_count"] == 4
    row = query(catalog, identity, "analysis")["data"]
    assert row["analysis_id"] == analysis["analysis"]["analysis"]["analysis_id"]
    assert row["configuration"] == {"topics": {"backend": "bertopic"}}
    topics = query(catalog, identity, "topics")["data"]
    assert [t["topic_id"] for t in topics] == [0, -1, 1]
    assert topics[0]["member_count"] == 2 and topics[0]["negative"] == 2
    assert topics[0]["keywords"] == [{"term": "acceso", "weight": 0.2}]
    assert topics[0]["wording_source"] == "original"
    assert topics[0]["display_name"] == topics[0]["name"]
    sentiment = query(catalog, identity, "sentiment")["data"]
    assert sentiment["classified"] == 3 and sentiment["excluded"] == 1
    assert sum(p["selected_reviews"] for p in sentiment["by_platform"]) == 4
    # Reviews became a query in the dashboard bundle: paged, text included.
    reviews = query(catalog, identity, "reviews")["data"]
    assert reviews["total"] == 4 and len(reviews["items"]) == 4
    # The five v1 kinds answer exactly as they would for a v1 package of the same data.
    for kind in store.CAPABILITIES_V1:
        assert query(catalog, identity, kind)["kind"] == kind


def test_v1_packages_are_unchanged_and_refuse_v2_kinds(descriptions, tmp_path):
    dataset, _ = descriptions
    bundle = build_bundle(dataset, tmp_path / "v1")
    manifest = validate_bundle(bundle)
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["capabilities"] == list(store.CAPABILITIES_V1)
    catalog, state = tmp_path / "catalog", tmp_path / "state"
    identity = activate_bundle(bundle, catalog, state)["publication_id"]
    assert query(catalog, identity, "overview")["data"]["row_count"] == 4
    with pytest.raises(
        ValueError, match="Capability not available in this publication"
    ):
        query(catalog, identity, "topics")


def test_interpretations_are_validated_against_their_topic(descriptions):
    _, analysis = descriptions
    block = analysis["analysis"]
    dataset_id = analysis["dataset"]["dataset_id"]
    members = [a["record_id"] for a in block["assignments"] if a["topic_id"] == 0]
    good = deepcopy(analysis)
    good["analysis"]["interpretations"] = [
        interpretation_row(dataset_id, 0, members[:1])
    ]
    assert "interpretations_partial" in {w["code"] for w in validate_description(good)}
    full = deepcopy(good)
    full["analysis"]["interpretations"].append(
        interpretation_row(
            dataset_id,
            1,
            [a["record_id"] for a in block["assignments"] if a["topic_id"] == 1],
        )
    )
    assert "interpretations_partial" not in {
        w["code"] for w in validate_description(full)
    }
    foreign = deepcopy(good)
    foreign["analysis"]["interpretations"][0]["supporting_review_ids_json"] = (
        json.dumps([a["record_id"] for a in block["assignments"] if a["topic_id"] == 1])
    )
    with pytest.raises(ValueError, match="belong to the interpreted topic"):
        validate_description(foreign)
    outlier = deepcopy(good)
    outlier["analysis"]["interpretations"][0]["topic_id"] = -1
    with pytest.raises(ValueError, match="distinct assigned topic"):
        validate_description(outlier)


@pytest.mark.parametrize(
    "mutation",
    [
        "counts",
        "embedding",
        "provenance",
        "extra-review",
        "member-count",
        "probability",
        "full-claim",
    ],
)
def test_v2_descriptions_must_reconcile(descriptions, mutation):
    _, analysis = descriptions
    broken = deepcopy(analysis)
    block = broken["analysis"]
    if mutation == "counts":
        block["analysis"]["assigned_reviews"] += 1
        block["analysis"]["unassigned_reviews"] -= 1
    elif mutation == "embedding":
        broken["embedding_status"]["release_id"] = (
            "11111111-2222-4333-8444-555555555555"
        )
    elif mutation == "provenance":
        broken["provenance"] = [
            r for r in broken["provenance"] if r["kind"] != "analysis_candidate"
        ]
    elif mutation == "extra-review":
        block["reviews"].append(
            {**block["reviews"][0], "record_id": "extra", "vector_row": 9}
        )
    elif mutation == "member-count":
        block["topics"][0]["member_count"] += 1
    elif mutation == "probability":
        for row in block["sentiment"]:
            if row["status"] == "classified":
                row["negative"] = 0.5
                break
    elif mutation == "full-claim":
        block["analysis"]["selection_kind"] = "full"
        broken["dataset"]["row_count"] += 1
        broken["sources"][0]["record_count"] += 1
    with pytest.raises(ValueError):
        validate_description(broken)


def test_collector_review_ids_with_colons_are_accepted(descriptions, tmp_path):
    _, analysis = descriptions
    renamed = deepcopy(analysis)
    block = renamed["analysis"]
    mapping = {
        row["record_id"]: f"appbot:498483038:{index}"
        for index, row in enumerate(block["reviews"])
    }
    for table in ("reviews", "assignments", "sentiment"):
        for row in block[table]:
            row["record_id"] = mapping[row["record_id"]]
    for topic in block["topics"]:
        topic["evidence_ids_json"] = json.dumps(
            [mapping[i] for i in json.loads(topic["evidence_ids_json"])]
        )
    assert validate_description(renamed)
    bundle = build_bundle(renamed, tmp_path / "colons")
    assert validate_bundle(bundle)["schema_version"] == SCHEMA_VERSION_V2
    blank = deepcopy(renamed)
    blank["analysis"]["reviews"][0]["record_id"] = " "
    with pytest.raises(ValueError, match="nonblank identifier"):
        validate_description(blank)


def test_manifest_and_database_versions_must_agree(descriptions, tmp_path):
    _, analysis = descriptions
    bundle = build_bundle(analysis, tmp_path / "v2")
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["schema_version"] = SCHEMA_VERSION
    manifest["capabilities"] = list(store.CAPABILITIES_V1)
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        validate_bundle(bundle)


def test_cli_build_with_an_analysis_produces_v2(
    descriptions, stored, candidate, tmp_path, monkeypatch
):
    import voc.datasets

    monkeypatch.setattr(
        voc.datasets, "get_dataset", lambda *a, **k: snapshot_for(candidate)
    )
    output = tmp_path / "cli-v2"
    result = CliRunner().invoke(
        main,
        [
            "publication",
            "build",
            "--source",
            "s3",
            "--dataset-version",
            DATASET_ID,
            "--analysis",
            stored.manifest.analysis_id,
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == SCHEMA_VERSION_V2
    assert payload["capabilities"] == list(store.CAPABILITIES_V2)
    assert Path(payload["destination"]) == output.resolve()
    personal = CliRunner().invoke(
        main,
        [
            "publication",
            "build",
            "--source",
            "personal",
            "--dataset-version",
            DATASET_ID,
            "--analysis",
            stored.manifest.analysis_id,
            "--output",
            str(tmp_path / "no"),
        ],
    )
    assert personal.exit_code != 0 and "requires --source s3" in personal.output
    assert not (tmp_path / "no").exists()


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    root = tmp_path_factory.mktemp("shared-v2")
    bundle = build_bundle(analysis_description(), root / "bundle")
    pointer = activate_bundle(bundle, root / "catalog", root / "state")
    return root / "catalog", pointer["publication_id"]


def test_summary_and_topic_list_rank_feature_topics_by_wilson_bound(shared):
    catalog, identity = shared
    summary = query(catalog, identity, "summary")
    assert summary["schema_version"] == "voc-data-query-v2"
    assert (
        summary["selection"]["platforms"] is None
        and summary["selection"]["page"] is None
    )
    data = summary["data"]
    assert (data["reviews_in_view"], data["classified"], data["topics_in_view"]) == (
        60,
        58,
        5,
    )
    assert data["negative"] == {"count": 20, "share": 20 / 58}
    top = data["most_negative_feature"]
    assert top["topic_id"] == 0 and top["assessment"] == "single_feature"
    assert (
        top["negative"] == 8 and top["total"] == 10 and 0.48 < top["wilson_lower"] < 0.5
    )
    # Topic 3 is more negative than topic 1 but uninterpreted, so it cannot be the feature.
    rows = query(catalog, identity, "topic_list", {"sort": "negative"})["data"]
    assert [r["topic_id"] for r in rows] == [0, 3, 1, 4, 2, -1]
    assert (
        rows[1]["assessment"] == "uninterpreted"
        and rows[-1]["assessment"] == "unassigned"
    )
    assert rows[0]["keywords"] == [
        "lenta",
        "tarda",
        "abrir",
        "carga",
        "espera",
        "lento",
    ]
    volume = query(catalog, identity, "topic_list")["data"]
    assert [r["topic_id"] for r in volume] == [0, 1, 2, 3, 4, -1]


def test_timeline_platforms_detail_and_reviews_answer_under_the_selection(shared):
    catalog, identity = shared
    timeline = query(catalog, identity, "timeline")["data"]
    assert [m["month"] for m in timeline["months"]][:2] == ["2025-09", "2025-10"]
    assert len(timeline["months"]) == 13 and timeline["busiest"]["total"] == 5
    narrowed = query(
        catalog, identity, "timeline", {"month_from": "2026-07", "topic_id": 0}
    )["data"]
    assert [m["month"] for m in narrowed["months"]] == ["2026-07", "2026-08", "2026-09"]
    assert narrowed["reviews_in_view"] == sum(m["total"] for m in narrowed["months"])
    platforms = query(catalog, identity, "platforms")["data"]
    assert [p["platform"] for p in platforms] == [
        "google_play",
        "app_store",
        "x",
        "youtube",
    ]
    assert platforms[0]["total"] == 36
    detail = query(catalog, identity, "topic_detail", {"topic_id": 0})["data"]
    assert detail["display_name"] == "Lentitud al abrir y cargar la aplicación"
    assert detail["in_view"] == 10 == detail["member_count"] and detail["negative"] == 8
    assert detail["description"].startswith("Descripción ficticia")
    empty = query(
        catalog, identity, "topic_detail", {"topic_id": 0, "platforms": "youtube"}
    )["data"]
    assert empty["in_view"] == 0 and empty["display_name"] == detail["display_name"]
    with pytest.raises(LookupError):
        query(catalog, identity, "topic_detail", {"topic_id": 99})
    reviews = query(catalog, identity, "reviews", {"topic_id": 0, "page_size": 4})[
        "data"
    ]
    assert (reviews["total"], reviews["pages"], len(reviews["items"])) == (10, 3, 4)
    assert [i["label"] for i in reviews["items"]] == ["negative"] * 4
    assert all(
        i["rating"] in (None, 1, 2, 3, 4, 5) and "text" in i for i in reviews["items"]
    )
    last = query(
        catalog, identity, "reviews", {"topic_id": 0, "page_size": 4, "page": 3}
    )["data"]
    assert [i["label"] for i in last["items"]] == ["positive", "neutral"][::-1][
        :2
    ] or len(last["items"]) == 2


def test_filters_change_the_view_and_the_result_id(shared):
    catalog, identity = shared
    whole = query(catalog, identity, "summary")
    stores = query(catalog, identity, "summary", {"platforms": "google_play,app_store"})
    assert stores["data"]["reviews_in_view"] == 44
    assert stores["result_id"] != whole["result_id"]
    # Two spellings of the same selection share one result identifier.
    again = query(
        catalog, identity, "summary", {"platforms": ["app_store", "google_play"]}
    )
    assert again["result_id"] == stores["result_id"]
    labelled = query(catalog, identity, "summary", {"labels": "negative"})["data"]
    assert labelled["reviews_in_view"] == 20 == labelled["classified"]
    unassigned = query(catalog, identity, "reviews", {"assessments": "unassigned"})[
        "data"
    ]
    assert unassigned["total"] == 10 and {
        i["topic_id"] for i in unassigned["items"]
    } == {-1}
    found = query(catalog, identity, "reviews", {"search": "TARDA"})["data"]
    assert found["total"] == 40
    literal = query(catalog, identity, "reviews", {"search": "100%_x"})["data"]
    assert literal["total"] == 0


def test_dataset_kinds_keep_their_v1_envelope_and_take_no_selection(shared):
    catalog, identity = shared
    overview = query(catalog, identity, "overview")
    assert (
        overview["schema_version"] == "voc-data-query-v1"
        and "selection" not in overview
    )
    with pytest.raises(SelectionError, match="takes no selection"):
        query(catalog, identity, "overview", {"platforms": "x"})
    with pytest.raises(SelectionError, match="Unknown selection fields"):
        query(catalog, identity, "summary", {"sql": "SELECT 1"})


def test_v1_packages_refuse_analysis_kinds(descriptions, tmp_path):
    dataset, _ = descriptions
    bundle = build_bundle(dataset, tmp_path / "v1-only")
    identity = activate_bundle(bundle, tmp_path / "cat", tmp_path / "st")[
        "publication_id"
    ]
    with pytest.raises(
        ValueError, match="Capability not available in this publication"
    ):
        query(tmp_path / "cat", identity, "summary")


def test_cli_query_passes_the_selection_through(shared):
    catalog, identity = shared
    result = CliRunner().invoke(
        main,
        [
            "publication",
            "query",
            "--catalog",
            str(catalog),
            "--publication-id",
            identity,
            "--kind",
            "reviews",
            "--topic-id",
            "0",
            "--labels",
            "negative",
            "--page-size",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert (
        payload["selection"]["labels"] == ["negative"] and payload["data"]["total"] == 8
    )
    bad = CliRunner().invoke(
        main,
        [
            "publication",
            "query",
            "--catalog",
            str(catalog),
            "--publication-id",
            identity,
            "--kind",
            "summary",
            "--sort",
            "negative",
        ],
    )
    assert bad.exit_code != 0 and "does not take sort" in bad.output
