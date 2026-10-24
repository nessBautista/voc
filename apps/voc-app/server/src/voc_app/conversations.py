"""Persist bounded turns independently of HTTP sockets and published DuckDB files."""

import asyncio
import fcntl
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from .agent.contracts import AgentError
from .agent.graph import (
    DAILY_RECURSION_LIMIT,
    RESEARCH_RECURSION_LIMIT,
    RESEARCH_TIMEOUT_SECONDS,
    build_graph,
)
from .agent.memory import remember

logger = logging.getLogger(__name__)


class ConversationError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def timestamp():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def visible(row_owner, caller):
    """Rows belong to their owner; rows written before ownership belong to local mode."""
    return row_owner == caller.owner or (row_owner is None and caller.source == "local")


def inaccessible():
    # One answer for "missing" and "someone else's", so nothing is disclosed.
    return ConversationError(
        404, "conversation_not_found", "No existe esa conversación."
    )


def backup(path, version):
    """Copy a database before changing its schema; the copy is consistent even under WAL."""
    destination = path.with_name(f"{path.stem}.v{version}-backup{path.suffix}")
    if destination.exists():
        return destination
    with sqlite3.connect(path) as source, sqlite3.connect(destination) as copy:
        source.backup(copy)
    return destination


class Conversations:
    # Per-owner limits apply before any model request is made.
    RATE_LIMIT = 30
    RATE_WINDOW = timedelta(minutes=10)
    # A class attribute so the test suite can shorten it; requests cannot change it.
    RESEARCH_TIMEOUT_SECONDS = RESEARCH_TIMEOUT_SECONDS

    def __init__(self, settings, client, planner):
        self.settings, self.client = settings, client
        self.graph = build_graph(planner, client)
        self.path = settings.state_dir / "conversations.sqlite3"
        self.lock = None
        self.admission = asyncio.Lock()
        self.tasks = {}

    @contextmanager
    def database(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def start(self):
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        # One process owns background turns. A second worker must not interrupt them.
        self.lock = (self.settings.state_dir / "conversations.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.database() as db:
                db.execute("PRAGMA journal_mode=WAL")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, 2):
                    raise ValueError("Unsupported conversation database version")
            if version == 1:
                # Version 2 adds the owner; existing rows keep no owner and stay local.
                backup(self.path, version)
                with self.database() as db:
                    db.execute("ALTER TABLE conversations ADD COLUMN owner TEXT")
            with self.database() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id TEXT PRIMARY KEY, publication_id TEXT NOT NULL,
                        created_at TEXT NOT NULL, owner TEXT
                    );
                    CREATE TABLE IF NOT EXISTS turns (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL UNIQUE,
                        conversation_id TEXT NOT NULL REFERENCES conversations(id),
                        request TEXT NOT NULL, question TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
                        planner_mode TEXT NOT NULL, answer TEXT, error TEXT,
                        created_at TEXT NOT NULL, finished_at TEXT
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS one_running_turn
                        ON turns(conversation_id) WHERE status = 'running';
                    PRAGMA user_version=2;
                """)
                db.execute(
                    "UPDATE turns SET status='interrupted', error=?, finished_at=? WHERE status='running'",
                    (
                        json.dumps(
                            {
                                "code": "interrupted",
                                "message": "El servidor se reinició. Esta pregunta no se ejecutará otra vez automáticamente.",
                            }
                        ),
                        timestamp(),
                    ),
                )
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    def read_conversation(self, identity, caller):
        with self.database() as db:
            # Keep the header and turns in one snapshot, including report creation.
            db.execute("BEGIN")
            row = db.execute(
                "SELECT * FROM conversations WHERE id=?", (identity,)
            ).fetchone()
            if row is None or not visible(row["owner"], caller):
                raise inaccessible()
            turns = db.execute(
                "SELECT * FROM turns WHERE conversation_id=? ORDER BY sequence",
                (identity,),
            ).fetchall()
        return {
            "schema_version": "voc-conversation-v1",
            "conversation_id": row["id"],
            "publication_id": row["publication_id"],
            "created_at": row["created_at"],
            "turns": [
                {
                    "request_id": turn["request_id"],
                    "question": turn["question"],
                    "status": turn["status"],
                    "planner_mode": turn["planner_mode"],
                    "answer": json.loads(turn["answer"]) if turn["answer"] else None,
                    "error": json.loads(turn["error"]) if turn["error"] else None,
                    "created_at": turn["created_at"],
                    "finished_at": turn["finished_at"],
                }
                for turn in turns
            ],
        }

    def owner_usage(self, caller):
        """How many turns the caller has running, and how many it started recently."""
        with self.database() as db:
            running, recent = db.execute(
                """SELECT SUM(t.status='running'), SUM(t.created_at > ?)
                   FROM turns t JOIN conversations c ON c.id = t.conversation_id
                   WHERE c.owner = ?""",
                (
                    (datetime.now(UTC) - self.RATE_WINDOW)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    caller.owner,
                ),
            ).fetchone()
        return running or 0, recent or 0

    async def ask(self, request, caller):
        encoded = json.dumps(request.model_dump(), sort_keys=True, ensure_ascii=False)
        async with self.admission:
            with self.database() as db:
                saved = db.execute(
                    """SELECT t.*, c.owner FROM turns t
                       JOIN conversations c ON c.id = t.conversation_id
                       WHERE t.request_id=?""",
                    (request.request_id,),
                ).fetchone()
            if saved:
                # A copied request ID reveals nothing and replays nothing across owners.
                if not visible(saved["owner"], caller):
                    raise inaccessible()
                if saved["request"] != encoded:
                    raise ConversationError(
                        409,
                        "request_conflict",
                        "Ese identificador ya corresponde a otra petición.",
                    )
                return self.read_conversation(saved["conversation_id"], caller)
            if len(self.tasks) >= 8:
                raise ConversationError(
                    429,
                    "chat_busy",
                    "El chat está ocupado. Reintenta esta misma petición más tarde.",
                )
            identity = request.conversation_id
            if identity:
                conversation = self.read_conversation(identity, caller)
                if any(turn["status"] == "running" for turn in conversation["turns"]):
                    raise ConversationError(
                        409,
                        "conversation_busy",
                        "Espera a que termine la pregunta anterior.",
                    )
                if len(conversation["turns"]) >= 100:
                    raise ConversationError(
                        409,
                        "conversation_full",
                        "Esta conversación llegó a 100 preguntas. Abre una nueva.",
                    )
            # The person's own limits come next, before any publication or model work.
            running, recent = self.owner_usage(caller)
            if running:
                raise ConversationError(
                    429, "owner_busy", "Espera a que termine tu pregunta anterior."
                )
            if recent >= self.RATE_LIMIT:
                raise ConversationError(
                    429,
                    "rate_limited",
                    "Has enviado muchas preguntas seguidas. Espera unos minutos.",
                )
            if identity:
                publication = conversation["publication_id"]
            else:
                # Selection happens before reserving a turn; no SQLite transaction spans I/O.
                publication = await self.client.current()
                identity = str(
                    uuid5(NAMESPACE_URL, "voc-conversation:" + request.request_id)
                )
                conversation = {"turns": []}
            history = [
                {"question": turn["question"], "plan": turn["answer"]["plan"]}
                for turn in conversation["turns"]
                if turn["status"] == "completed"
            ][-4:]
            # What earlier answers named, for topic references; the model never sees it.
            memory = remember(conversation["turns"])
            with self.database() as db:
                if request.conversation_id is None:
                    db.execute(
                        "INSERT INTO conversations (id, publication_id, created_at, owner) VALUES (?,?,?,?)",
                        (identity, publication, timestamp(), caller.owner),
                    )
                else:
                    # A conversation from before ownership is claimed on its next question.
                    db.execute(
                        "UPDATE conversations SET owner=? WHERE id=? AND owner IS NULL",
                        (caller.owner, identity),
                    )
                db.execute(
                    "INSERT INTO turns (request_id,conversation_id,request,question,status,planner_mode,created_at) VALUES (?,?,?,?,'running',?,?)",
                    (
                        request.request_id,
                        identity,
                        encoded,
                        request.question,
                        self.settings.planner_mode,
                        timestamp(),
                    ),
                )
            # Keep a strong task reference; client disconnects do not cancel work.
            task = asyncio.create_task(self.run(request, publication, history, memory))
            self.tasks[request.request_id] = task
            task.add_done_callback(
                lambda finished: self.tasks.pop(request.request_id, None)
            )
            return self.read_conversation(identity, caller)

    async def run(self, request, publication, history, memory):
        research = request.mode == "research"
        timeout = (
            self.RESEARCH_TIMEOUT_SECONDS
            if research
            else self.settings.turn_timeout_seconds
        )
        try:
            async with asyncio.timeout(timeout):
                result = await self.graph.ainvoke(
                    {
                        "question": request.question,
                        "history": history,
                        "memory": memory,
                        "publication_id": publication,
                        "mode": request.mode,
                    },
                    config={
                        "recursion_limit": RESEARCH_RECURSION_LIMIT
                        if research
                        else DAILY_RECURSION_LIMIT
                    },
                )
            self.finish(request.request_id, "completed", answer=result["answer"])
        except TimeoutError:
            self.finish(
                request.request_id,
                "failed",
                error={
                    "code": "turn_timeout",
                    "message": "La pregunta excedió el tiempo disponible. Puedes enviar una nueva petición.",
                },
            )
        except asyncio.CancelledError:
            self.finish(
                request.request_id,
                "interrupted",
                error={
                    "code": "interrupted",
                    "message": "La ejecución se interrumpió; no se repetirá automáticamente.",
                },
            )
            raise
        except AgentError as error:
            self.finish(
                request.request_id,
                "failed",
                error={"code": error.code, "message": error.message},
            )
        except Exception as error:  # noqa: BLE001 - persist a terminal failure for every task.
            # Provider text and graph state must never be returned as error detail;
            # the log keeps the exception type and, for our own ValueErrors, its message.
            logger.warning(
                "Turn %s failed before an answer: %s%s",
                request.request_id,
                type(error).__name__,
                f": {error}" if type(error) is ValueError else "",
            )
            self.finish(
                request.request_id,
                "failed",
                error={
                    "code": "invalid_answer",
                    "message": "No pudimos validar una respuesta con evidencia. Reformula la pregunta.",
                },
            )

    def finish(self, request_id, status, answer=None, error=None):
        with self.database() as db:
            db.execute(
                "UPDATE turns SET status=?,answer=?,error=?,finished_at=? WHERE request_id=? AND status='running'",
                (
                    status,
                    json.dumps(answer, ensure_ascii=False) if answer else None,
                    json.dumps(error, ensure_ascii=False) if error else None,
                    timestamp(),
                    request_id,
                ),
            )

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.lock:
            self.lock.close()
            self.lock = None
