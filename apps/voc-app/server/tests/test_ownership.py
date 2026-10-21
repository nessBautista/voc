"""Conversations and reports belong to one verified person; nobody else can see them."""

import asyncio
import sqlite3
import time
from uuid import uuid4

import pytest
from fake_cognito import DOMAIN, ISSUER, NATIVE, WEB, access_token
from fastapi.testclient import TestClient

from voc_app.agent.planner import Planner
from voc_app.conversations import Conversations
from voc_app.main import create_app
from voc_app.settings import Settings

QUESTION = "¿Cuántos registros tenemos de Google Play?"


def bearer(key, subject):
    return {"Authorization": "Bearer " + access_token(key, sub=subject)}


def payload(question=QUESTION, conversation=None, request_id=None):
    return {
        "request_id": request_id or str(uuid4()),
        "question": question,
        "conversation_id": conversation,
    }


def wait(client, identity, headers):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/conversations/{identity}", headers=headers)
        assert response.status_code == 200, response.text
        conversation = response.json()
        if not any(turn["status"] == "running" for turn in conversation["turns"]):
            return conversation
        time.sleep(0.02)
    pytest.fail("Conversation did not finish")


def ask(client, request, headers):
    response = client.post("/api/v1/ask", json=request, headers=headers)
    assert response.status_code == 202, response.text
    return wait(client, response.json()["conversation_id"], headers)


def error_code(response):
    return response.status_code, response.json()["error"]["code"]


def test_users_cannot_read_replay_or_export_each_others_history(
    web, key, cognito_active
):
    a, b = bearer(key, "user-a"), bearer(key, "user-b")
    first = payload()
    conversation = ask(web, first, a)
    identity = conversation["conversation_id"]
    assert conversation["turns"][0]["status"] == "completed"

    # B sees neither the conversation nor a hint that it exists.
    assert error_code(web.get(f"/api/v1/conversations/{identity}", headers=b)) == (
        404,
        "conversation_not_found",
    )
    assert error_code(
        web.post("/api/v1/ask", json=payload(conversation=identity), headers=b)
    ) == (404, "conversation_not_found")
    # A copied request ID with the identical body replays nothing for B.
    assert error_code(web.post("/api/v1/ask", json=first, headers=b)) == (
        404,
        "conversation_not_found",
    )
    assert len(wait(web, identity, a)["turns"]) == 1
    # A still retries idempotently with the same ID.
    assert web.post("/api/v1/ask", json=first, headers=a).status_code == 200

    export = {
        "request_id": str(uuid4()),
        "publication_id": cognito_active["publication_id"],
    }
    assert error_code(
        web.post(f"/api/v1/conversations/{identity}/reports", json=export, headers=b)
    ) == (404, "conversation_not_found")
    prepared = web.post(
        f"/api/v1/conversations/{identity}/reports", json=export, headers=a
    )
    assert prepared.status_code == 200, prepared.text
    report = prepared.json()["report_id"]
    assert web.get(f"/api/v1/reports/{report}", headers=a).status_code == 200
    assert error_code(web.get(f"/api/v1/reports/{report}", headers=b)) == (
        404,
        "report_not_found",
    )
    assert error_code(web.get(f"/api/v1/reports/{report}/chunks", headers=b)) == (
        404,
        "report_not_found",
    )
    # Reusing A's export request ID does not hand B the report either.
    assert error_code(
        web.post(f"/api/v1/conversations/{identity}/reports", json=export, headers=b)
    ) == (404, "report_not_found")
    with sqlite3.connect(web.app.state.settings.state_dir / "reports.sqlite3") as db:
        owners = db.execute("SELECT owner FROM reports").fetchall()
    assert owners == [(f"{ISSUER}#user-a",)]


def test_one_running_turn_and_a_rate_limit_per_user(
    cognito_settings, cognito, cognito_active, key, monkeypatch
):
    class SlowPlanner:
        def __init__(self):
            self.delegate = Planner(cognito_settings)

        async def plan(self, question, history):
            await asyncio.sleep(0.3)
            return await self.delegate.plan(question, history)

    monkeypatch.setattr(Conversations, "RATE_LIMIT", 3)
    app = create_app(
        cognito_settings, planner=SlowPlanner(), oidc_transport=cognito.transport
    )
    a, b = bearer(key, "user-a"), bearer(key, "user-b")
    with TestClient(app) as web:
        started = web.post("/api/v1/ask", json=payload(), headers=a)
        assert started.status_code == 202
        # A second question from the same person waits; another person is unaffected.
        assert error_code(web.post("/api/v1/ask", json=payload(), headers=a)) == (
            429,
            "owner_busy",
        )
        assert web.post("/api/v1/ask", json=payload(), headers=b).status_code == 202
        wait(web, started.json()["conversation_id"], a)
        ask(web, payload(), a)
        ask(web, payload(), a)
        assert error_code(web.post("/api/v1/ask", json=payload(), headers=a)) == (
            429,
            "rate_limited",
        )


def v1_databases(state_dir, publication_id):
    """Write the schema the reference implementation used before ownership existed."""
    state_dir.mkdir(parents=True, exist_ok=True)
    conversation, request = str(uuid4()), str(uuid4())
    with sqlite3.connect(state_dir / "conversations.sqlite3") as db:
        db.executescript("""
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, publication_id TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE turns (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                request TEXT NOT NULL, question TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
                planner_mode TEXT NOT NULL, answer TEXT, error TEXT,
                created_at TEXT NOT NULL, finished_at TEXT
            );
            PRAGMA user_version=1;
        """)
        db.execute(
            "INSERT INTO conversations VALUES (?,?,?)",
            (conversation, publication_id, "2026-01-01T00:00:00Z"),
        )
        db.execute(
            "INSERT INTO turns (request_id,conversation_id,request,question,status,planner_mode,created_at,finished_at,error) VALUES (?,?,?,?,'failed','fixture',?,?,?)",
            (
                request,
                conversation,
                "{}",
                "pregunta antigua",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:01Z",
                '{"code":"turn_timeout","message":"x"}',
            ),
        )
    with sqlite3.connect(state_dir / "reports.sqlite3") as db:
        db.executescript("""
            CREATE TABLE reports (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL, metadata TEXT NOT NULL,
                snapshot BLOB NOT NULL, markdown BLOB NOT NULL,
                UNIQUE(conversation_id, snapshot_sha256)
            );
            CREATE TABLE requests (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                publication_id TEXT NOT NULL, report_id TEXT NOT NULL REFERENCES reports(id)
            );
            PRAGMA user_version=1;
        """)
    return conversation


def test_version_one_databases_migrate_with_a_backup_and_stay_local(
    settings, active, key, cognito
):
    legacy = v1_databases(settings.state_dir, active["publication_id"])
    with TestClient(create_app(settings)) as local:
        # The developer still sees history written before ownership existed.
        assert local.get(f"/api/v1/conversations/{legacy}").status_code == 200
    for name in ("conversations", "reports"):
        assert (settings.state_dir / f"{name}.v1-backup.sqlite3").is_file()
        with sqlite3.connect(settings.state_dir / f"{name}.sqlite3") as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    with sqlite3.connect(settings.state_dir / "conversations.v1-backup.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1

    hosted = Settings(
        settings.publications_dir,
        settings.state_dir,
        auth_mode="cognito",
        cognito_issuer=ISSUER,
        cognito_domain=DOMAIN,
        cognito_web_client_id=WEB,
        cognito_native_client_id=NATIVE,
        cognito_web_client_secret="test-secret-value",
    )
    with TestClient(create_app(hosted, oidc_transport=cognito.transport)) as web:
        # Nobody inherits the unowned history in cognito mode.
        assert error_code(
            web.get(f"/api/v1/conversations/{legacy}", headers=bearer(key, "user-a"))
        ) == (404, "conversation_not_found")


def test_a_local_question_claims_an_unowned_conversation(settings, active):
    legacy = v1_databases(settings.state_dir, active["publication_id"])
    with TestClient(create_app(settings)) as local:
        response = local.post("/api/v1/ask", json=payload(conversation=legacy))
        assert response.status_code == 202, response.text
    with sqlite3.connect(settings.state_dir / "conversations.sqlite3") as db:
        assert db.execute("SELECT owner FROM conversations").fetchone() == (
            "local#developer",
        )
