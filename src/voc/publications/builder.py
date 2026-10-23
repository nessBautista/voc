"""Build a complete local publication bundle from normalized table rows."""

import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb

from voc import __version__

from .contracts import schema_version_for, validate_database, validate_description
from .schema import SCHEMA_SQL_BY_VERSION, TABLE_COLUMNS_BY_VERSION, V2_TABLE_COLUMNS
from .store import (
    CAPABILITIES_BY_VERSION,
    DATABASE_NAME,
    MANIFEST_NAME,
    file_sha256,
    validate_bundle,
)


def producer_info():
    """Record the producing checkout when available, without publishing its path."""
    revision, dirty = None, None
    root = Path(__file__).resolve().parents[3]
    if (root / ".git").exists() and (root / "pyproject.toml").is_file():
        try:
            # Capture Git output privately; only revision and dirty state are exported.
            revision = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            status = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "status",
                    "--porcelain",
                    "--untracked-files=normal",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            dirty = bool(status.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            revision, dirty = None, None
    return {
        "voc_version": __version__,
        "duckdb_version": duckdb.__version__,
        "code_revision": revision,
        "code_dirty": dirty,
    }


@contextmanager
def _build_lock(parent):
    # Keep one stable lock inode; unlinking it can let concurrent writers bypass it.
    with (parent / ".publication-build.lock").open("a+b") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as error:
            raise ValueError(
                "Another publication build is using this directory"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_UN,
            )


def _require_new_destination(destination):
    # Include dangling symlinks: they are existing entries, not free output names.
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Publication destination already exists: {destination}")


def _write_database(path, description, publication):
    version = publication["schema_version"]
    with duckdb.connect(str(path)) as connection:
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(SCHEMA_SQL_BY_VERSION[version])
            # Insert parent tables first; identifiers are fixed and values are bound.
            for table, columns in TABLE_COLUMNS_BY_VERSION[version].items():
                if table == "publication":
                    rows = [publication]
                elif table in ("dataset", "embedding_status"):
                    rows = [description[table]]
                elif table in V2_TABLE_COLUMNS:
                    block = description["analysis"]
                    rows = [block[table]] if table == "analysis" else block[table]
                else:
                    rows = description[table]
                if rows:
                    connection.executemany(
                        f'INSERT INTO "{table}" ({", ".join(columns)}) '
                        f"VALUES ({', '.join('?' for _ in columns)})",
                        [[row[column] for column in columns] for row in rows],
                    )
            # Validate what DuckDB actually stored before committing any rows.
            validate_database(connection)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("CHECKPOINT")
    # The connection is closed before its file is hashed or moved.


def build_bundle(description, destination):
    """Create an immutable two-file directory; refuse to replace existing output.

    The destination is a local path (normally under /workspace/data). No upload,
    acquisition, model execution or activation takes place here.
    """
    description = deepcopy(description)
    validate_description(description)
    if re.match(
        r"^[A-Za-z][A-Za-z0-9+.-]*:",
        os.fspath(destination),
    ):
        raise ValueError("Publication output must be a local directory, not a URI")
    destination = Path(destination).expanduser().absolute()
    _require_new_destination(destination)
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    destination = destination.parent.resolve() / destination.name

    # Serialize cooperating builders without exposing an incomplete final directory.
    with _build_lock(destination.parent):
        _require_new_destination(destination)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}-",
                dir=destination.parent,
            )
        )
        try:
            version = schema_version_for(description)
            publication = {
                "publication_id": str(uuid4()),
                "dataset_id": description["dataset"]["dataset_id"],
                "schema_version": version,
                "created_at": datetime.now(UTC).isoformat(),
            }
            database = staging / DATABASE_NAME
            _write_database(
                database,
                description,
                publication,
            )
            manifest = {
                "format_version": 1,
                **publication,
                "producer": producer_info(),
                "capabilities": list(CAPABILITIES_BY_VERSION[version]),
                "database": {
                    "path": DATABASE_NAME,
                    "size_bytes": database.stat().st_size,
                    "sha256": file_sha256(database),
                },
            }
            # Flush both files before making the directory visible to consumers.
            with database.open("rb") as stream:
                os.fsync(stream.fileno())
            with (staging / MANIFEST_NAME).open(
                "x",
                encoding="utf-8",
            ) as stream:
                json.dump(
                    manifest,
                    stream,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            validate_bundle(staging)
            _require_new_destination(destination)
            staging.rename(destination)
        finally:
            # Clean up only our temporary directory; never remove another output.
            if staging.exists():
                shutil.rmtree(staging)
    return destination
