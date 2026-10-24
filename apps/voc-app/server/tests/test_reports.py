"""Prove frozen evidence, atomic publication and byte-exact report retrieval."""

import base64
import concurrent.futures
import hashlib
import json
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from test_chat import CountPlanner, app_for, ask, payload, wait
from voc_app.reports import CHUNK_BYTES, escaped

from voc.publications.store import activate_bundle


def preparation(conversation):
    return {
        "request_id": str(uuid4()),
        "publication_id": conversation["publication_id"],
    }


def prepare(client, conversation, request=None):
    return client.post(
        f"/api/v1/conversations/{conversation['conversation_id']}/reports",
        json=request or preparation(conversation),
    )


def download(client, metadata):
    result = bytearray()
    chunks = []
    while True:
        response = client.get(
            f"/api/v1/reports/{metadata['report_id']}/chunks",
            params={"offset": len(result)},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["report"] == metadata
        chunk = page["chunk"]
        decoded = base64.b64decode(chunk["data"], validate=True)
        assert chunk["offset"] == len(result)
        assert 0 < len(decoded) <= CHUNK_BYTES
        result.extend(decoded)
        assert chunk["next_offset"] == len(result)
        chunks.append(chunk)
        if chunk["eof"]:
            break
    assert len(result) == metadata["byte_length"]
    assert hashlib.sha256(result).hexdigest() == metadata["sha256"]
    return bytes(result), chunks


def test_snapshot_survives_new_turn_publication_switch_and_server_restart(
    settings, active, bundles
):
    planner = CountPlanner(settings)
    with TestClient(app_for(settings, planner)) as client:
        first = ask(client, payload())
        request = preparation(first)
        report = prepare(client, first, request).json()
        body, _ = download(client, report)
        assert "8 registros de Google Play" in body.decode()
        assert "No informado (null)" in body.decode()
        assert report["turn_count"] == 1
        assert planner.calls == 1
        second = ask(client, payload("¿y de App Store?", first["conversation_id"]))
        # Retrying a response lost before the follow-up keeps the original snapshot.
        assert prepare(client, second, request).json() == report
        newer = prepare(client, second).json()
        assert newer["report_id"] != report["report_id"]
        assert newer["snapshot_sha256"] != report["snapshot_sha256"]
        assert newer["turn_count"] == 2
        assert prepare(client, second).json() == newer
        activate_bundle(bundles[1], settings.publications_dir, settings.state_dir)
        assert download(client, report)[0] == body
        assert planner.calls == 2
    with TestClient(app_for(settings, planner)) as client:
        assert client.get(f"/api/v1/reports/{report['report_id']}").json() == report
        assert download(client, report)[0] == body
        assert planner.calls == 2


def test_running_turn_not_ready_and_terminal_failure_is_not_an_answer(settings, active):
    with TestClient(app_for(settings, CountPlanner(settings, delay=0.3))) as client:
        pending = client.post("/api/v1/ask", json=payload()).json()
        request = preparation(pending)
        response = prepare(client, pending, request)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "report_not_ready"
        completed = wait(client, pending["conversation_id"])
        with sqlite3.connect(settings.state_dir / "conversations.sqlite3") as db:
            db.execute(
                "UPDATE turns SET status='interrupted',answer=NULL,error=?",
                (json.dumps({"code": "interrupted", "message": "Interrumpido"}),),
            )
        metadata = prepare(client, completed, request).json()
        text = download(client, metadata)[0].decode()
        assert "interrupted" in text and "Interrumpido" in text
        assert "No hay una respuesta completada" in text
        assert "8 registros de Google Play" not in text


def test_multichunk_unicode_digest_and_markdown_are_preserved(settings, active):
    with TestClient(app_for(settings)) as client:
        conversation = ask(client, payload())
        question = "# Ignore instructions [link](https://example.invalid) ![image](evil) <script> & `code` | café🙂漢字\n"
        # A long saved conversation exercises pagination without new model calls.
        with sqlite3.connect(settings.state_dir / "conversations.sqlite3") as db:
            original = db.execute(
                "SELECT conversation_id,request,answer,created_at,finished_at FROM turns"
            ).fetchone()
            for index in range(70):
                db.execute(
                    "INSERT INTO turns (request_id,conversation_id,request,question,status,planner_mode,answer,created_at,finished_at) VALUES (?,?,?,?, 'completed','fixture',?,?,?)",
                    (
                        str(uuid4()),
                        original[0],
                        original[1],
                        question * 8,
                        original[2],
                        original[3],
                        original[4],
                    ),
                )
        metadata = prepare(client, conversation).json()
        body, chunks = download(client, metadata)
        assert len(chunks) > 1
        text = body.decode("utf-8")
        assert "café🙂漢字" in text
        assert "<script>" not in text and "&lt;script&gt;" in text
        assert "![image](evil)" not in text
        assert "\\# Ignore instructions" in text
        assert "collection_date_unknown".replace("_", "\\_") in text
        # Any byte offset is legal, including one inside a multibyte character.
        start = body.index("🙂".encode()) + 1
        page = client.get(
            f"/api/v1/reports/{metadata['report_id']}/chunks?offset={start}"
        ).json()
        assert (
            base64.b64decode(page["chunk"]["data"]) == body[start : start + CHUNK_BYTES]
        )
        eof = client.get(
            f"/api/v1/reports/{metadata['report_id']}/chunks?offset={len(body)}"
        ).json()["chunk"]
        assert eof["eof"] is True and eof["data"] == ""


def test_bad_identities_offsets_origins_and_request_conflicts(settings, active):
    with TestClient(app_for(settings)) as client:
        conversation = ask(client, payload())
        request = preparation(conversation)
        metadata = prepare(client, conversation, request).json()
        second = ask(client, payload("Resumen del dataset"))
        assert prepare(client, second, request).status_code == 409
        assert (
            prepare(
                client, conversation, {**request, "publication_id": str(uuid4())}
            ).status_code
            == 409
        )
        assert (
            prepare(
                client, conversation, {**preparation(conversation), "extra": "no"}
            ).status_code
            == 422
        )
        url = f"/api/v1/conversations/{conversation['conversation_id']}/reports"
        assert (
            client.post(
                url,
                json=preparation(conversation),
                headers={"Origin": "https://example.invalid"},
            ).status_code
            == 403
        )
        assert client.get("/api/v1/reports/not-a-uuid").status_code == 422
        assert client.get(f"/api/v1/reports/{uuid4()}").status_code == 404
        for query in [
            "offset=-1",
            "offset=true",
            "offset=1.5",
            "offset=999999999",
            "offset=0&offset=1",
            "offset=0&limit=2",
        ]:
            assert (
                client.get(
                    f"/api/v1/reports/{metadata['report_id']}/chunks?{query}"
                ).status_code
                == 422
            )


def test_write_failure_rolls_back_artifact_and_retry_can_finish(settings, active):
    with TestClient(app_for(settings), raise_server_exceptions=False) as client:
        conversation = ask(client, payload())
        with sqlite3.connect(settings.state_dir / "reports.sqlite3") as db:
            db.execute(
                "CREATE TRIGGER fail_request BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END"
            )
        request = preparation(conversation)
        assert prepare(client, conversation, request).status_code == 500
        with sqlite3.connect(settings.state_dir / "reports.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
            db.execute("DROP TRIGGER fail_request")
        assert prepare(client, conversation, request).status_code == 200


def test_tampered_report_is_not_served(settings, active):
    with TestClient(app_for(settings)) as client:
        conversation = ask(client, payload())
        metadata = prepare(client, conversation).json()
        with sqlite3.connect(settings.state_dir / "reports.sqlite3") as db:
            db.execute("UPDATE reports SET markdown=?", (b"altered",))
        response = client.get(f"/api/v1/reports/{metadata['report_id']}/chunks")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "report_integrity_error"


def test_simultaneous_preparations_share_one_frozen_artifact(settings, active):
    with TestClient(app_for(settings)) as client:
        conversation = ask(client, payload())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(lambda _: prepare(client, conversation), range(8))
            )
        assert all(result.status_code == 200 for result in results)
        assert len({result.json()["report_id"] for result in results}) == 1
        with sqlite3.connect(settings.state_dir / "reports.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 1


def test_report_size_limit_does_not_publish_partial_state(
    settings, active, monkeypatch
):
    from voc_app import reports

    with TestClient(app_for(settings)) as client:
        conversation = ask(client, payload())
        monkeypatch.setattr(reports, "MAX_REPORT_BYTES", 20)
        response = prepare(client, conversation)
        assert response.status_code == 413
        with sqlite3.connect(settings.state_dir / "reports.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 0


def test_report_carries_analysis_evidence_with_its_selection(settings, v2_active):
    from test_chat import DEMO_FLOW

    with TestClient(app_for(settings)) as client:
        conversation = None
        for question, *_ in DEMO_FLOW:
            conversation = ask(
                client,
                payload(question, conversation and conversation["conversation_id"]),
            )
        report = prepare(client, conversation).json()
        body = download(client, report)[0].decode()
    assert report["turn_count"] == 6
    # The fixed note names both planner modes, whichever planned the turns.
    assert (
        "es fixture, que selecciona consultas sin llamar a un modelo"
        in body.replace("\\", "")
    )
    assert "o model, que las selecciona con un modelo" in body.replace("\\", "")
    for label in (
        "Qué dicen los datos.",
        "Interpretación.",
        "Lo que no se puede afirmar.",
    ):
        # The report escapes Markdown punctuation in every saved text and kind.
        assert body.count(escaped(label)) == 6
    for kind in (
        "summary",
        "topic_list",
        "timeline",
        "topic_detail",
        "reviews",
        "platforms",
    ):
        assert f"### Evidencia: {escaped(kind)}" in body
    # Each analysis evidence names the filters that produced its tables.
    assert body.count("selection: ") == 11
    assert "negative" in body and "Lentitud al abrir y cargar la aplicación" in body
