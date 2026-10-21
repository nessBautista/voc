"""Keep sign-in state and sessions on the server; the browser holds only an opaque cookie."""

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

LOGIN_TTL = timedelta(minutes=10)
# A session cannot outlive the pool's refresh token, which guide 03 recorded as five days.
SESSION_TTL = timedelta(days=5)


def now():
    return datetime.now(UTC)


def stamp(moment):
    return moment.isoformat().replace("+00:00", "Z")


def parse(text):
    return datetime.fromisoformat(text)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Sessions:
    def __init__(self, state_dir):
        self.path = state_dir / "sessions.sqlite3"

    @contextmanager
    def database(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.database() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS login_states (
                    state TEXT PRIMARY KEY, nonce TEXT NOT NULL,
                    code_verifier TEXT NOT NULL, return_to TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id_hash TEXT PRIMARY KEY, subject TEXT NOT NULL, email TEXT NOT NULL,
                    client_id TEXT NOT NULL, access_token TEXT NOT NULL,
                    refresh_token TEXT NOT NULL, access_expires_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
        self.purge()

    def purge(self):
        with self.database() as db:
            db.execute(
                "DELETE FROM login_states WHERE created_at < ?",
                (stamp(now() - LOGIN_TTL),),
            )
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (stamp(now()),))

    def begin_login(self, return_to="/"):
        """Record one pending sign-in and return its state, nonce and PKCE verifier."""
        if not return_to.startswith("/") or return_to.startswith("//"):
            raise ValueError("Return locations are application paths")
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        # token_urlsafe yields only unreserved characters, within PKCE's 43 to 128 length.
        verifier = secrets.token_urlsafe(64)
        with self.database() as db:
            db.execute(
                "INSERT INTO login_states VALUES (?,?,?,?,?)",
                (state, nonce, verifier, return_to, stamp(now())),
            )
        return state, nonce, verifier

    def take_login(self, state):
        """Consume a pending sign-in once; a second use or an old one returns None."""
        with self.database() as db:
            row = db.execute(
                "SELECT * FROM login_states WHERE state=?", (state,)
            ).fetchone()
            db.execute("DELETE FROM login_states WHERE state=?", (state,))
        if row is None or parse(row["created_at"]) < now() - LOGIN_TTL:
            return None
        return dict(row)

    def create(self, subject, email, client_id, tokens):
        """Store the tokens and return the cookie value; only its hash is kept."""
        value = secrets.token_urlsafe(32)
        created = now()
        with self.database() as db:
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    digest(value),
                    subject,
                    email,
                    client_id,
                    tokens["access_token"],
                    tokens["refresh_token"],
                    stamp(created + timedelta(seconds=tokens["expires_in"])),
                    stamp(created + SESSION_TTL),
                    stamp(created),
                ),
            )
        return value

    def read(self, value):
        with self.database() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE id_hash=?", (digest(value),)
            ).fetchone()
        if row is None or parse(row["expires_at"]) < now():
            return None
        return dict(row)

    def renew(self, value, tokens):
        with self.database() as db:
            db.execute(
                "UPDATE sessions SET access_token=?, refresh_token=COALESCE(?, refresh_token), access_expires_at=? WHERE id_hash=?",
                (
                    tokens["access_token"],
                    tokens.get("refresh_token"),
                    stamp(now() + timedelta(seconds=tokens["expires_in"])),
                    digest(value),
                ),
            )

    def delete(self, value):
        with self.database() as db:
            db.execute("DELETE FROM sessions WHERE id_hash=?", (digest(value),))
