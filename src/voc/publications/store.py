"""Install immutable bundles, select an active publication and read safely."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import duckdb

from .contracts import (
    _fields,
    _identifier,
    utc_datetime,
    validate_database,
    validate_publication,
)
from .schema import SCHEMA_SQL

CAPABILITIES = ("overview", "sources", "coverage", "freshness", "embeddings")
DATABASE_NAME = "publication.duckdb"
MANIFEST_NAME = "manifest.json"
MAX_MANIFEST_BYTES = 64 * 1024
READ_CONFIG = {
    "enable_external_access": False,
    "autoload_known_extensions": False,
    "autoinstall_known_extensions": False,
}


def file_sha256(path):
    """Hash closed files in bounded blocks, regardless of their eventual size."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _json_object(pairs):
    # Reject ambiguous manifests rather than silently accepting the last value.
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest field")
        result[key] = value
    return result


def _validate_manifest(manifest):
    _fields(
        manifest,
        (
            "format_version",
            "publication_id",
            "dataset_id",
            "schema_version",
            "created_at",
            "producer",
            "capabilities",
            "database",
        ),
        "manifest",
    )
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise ValueError("Unsupported manifest format_version")
    _identifier(
        manifest["dataset_id"],
        "dataset_id",
    )
    validate_publication(
        {
            key: manifest[key]
            for key in (
                "publication_id",
                "dataset_id",
                "schema_version",
                "created_at",
            )
        },
        manifest["dataset_id"],
    )
    if manifest["capabilities"] != list(CAPABILITIES):
        raise ValueError("Unsupported publication capabilities")

    # No caller-selected database path or query can be supplied by a manifest.
    database = manifest["database"]
    _fields(
        database,
        ("path", "size_bytes", "sha256"),
        "database",
    )
    if database["path"] != DATABASE_NAME:
        raise ValueError("Database path must be publication.duckdb")
    if type(database["size_bytes"]) is not int or database["size_bytes"] <= 0:
        raise ValueError("Invalid database size_bytes")
    if not isinstance(
        database["sha256"],
        str,
    ) or not re.fullmatch(
        r"[0-9a-f]{64}",
        database["sha256"],
    ):
        raise ValueError("Invalid database sha256")

    # Unknown code provenance is explicit when running from an installed wheel.
    producer = manifest["producer"]
    _fields(
        producer,
        ("voc_version", "duckdb_version", "code_revision", "code_dirty"),
        "producer",
    )
    for field in ("voc_version", "duckdb_version"):
        if not isinstance(
            producer[field],
            str,
        ) or not re.fullmatch(
            r"[0-9][A-Za-z0-9.+-]*",
            producer[field],
        ):
            raise ValueError("Invalid producer version")
    revision = producer["code_revision"]
    dirty = producer["code_dirty"]
    if revision is None:
        if dirty is not None:
            raise ValueError("Unknown revision requires unknown code_dirty")
    elif (
        not isinstance(
            revision,
            str,
        )
        or not re.fullmatch(
            r"[0-9a-f]{40,64}",
            revision,
        )
        or type(dirty) is not bool
    ):
        raise ValueError("Invalid producer code identity")


def _schema_signature(connection):
    # Read catalog metadata before reading any stored application rows.
    tables = connection.execute(
        "SELECT table_schema, table_name, table_type FROM information_schema.tables "
        "WHERE table_catalog = current_database() ORDER BY 1, 2"
    ).fetchall()
    columns = connection.execute(
        "SELECT table_schema, table_name, column_name, data_type, is_nullable "
        "FROM information_schema.columns WHERE table_catalog = current_database() "
        "ORDER BY table_schema, table_name, ordinal_position"
    ).fetchall()
    constraints = connection.execute(
        "SELECT table_name, constraint_type, expression, constraint_column_names, "
        "referenced_table, referenced_column_names FROM duckdb_constraints() "
        "WHERE database_name = current_database() "
        "ORDER BY table_name, constraint_index"
    ).fetchall()
    functions = connection.execute(
        "SELECT function_name FROM duckdb_functions() WHERE NOT internal "
        "AND database_name = current_database()"
    ).fetchall()
    return tables, columns, constraints, functions


@dataclass(frozen=True)
class OpenPublication:
    """Validated metadata and a connection that lives only inside its context."""

    connection: duckdb.DuckDBPyConnection
    manifest: dict
    warnings: list


def _checked_manifest(path):
    # Check package shape and integrity before opening the database.
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Bundle must be a real directory")
    if {item.name for item in path.iterdir()} != {DATABASE_NAME, MANIFEST_NAME}:
        raise ValueError(
            "Bundle must contain exactly publication.duckdb and manifest.json"
        )
    for name in (DATABASE_NAME, MANIFEST_NAME):
        item = path / name
        if item.is_symlink() or not item.is_file():
            raise ValueError("Bundle files must be regular files, not symlinks")

    # Verify bytes before opening the database or interpreting its tables.
    with (path / MANIFEST_NAME).open("rb") as stream:
        payload = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("Manifest exceeds the size limit")
    manifest = json.loads(
        payload,
        object_pairs_hook=_json_object,
    )
    _validate_manifest(manifest)
    database = path / DATABASE_NAME
    if (
        database.stat().st_size != manifest["database"]["size_bytes"]
        or file_sha256(database) != manifest["database"]["sha256"]
    ):
        raise ValueError("Database size or checksum differs from the manifest")

    return manifest


@contextmanager
def _open_bundle(path, *, expected_manifest_sha256=None):
    path = Path(path)
    manifest = _checked_manifest(path)
    if expected_manifest_sha256 is not None:
        _digest(
            expected_manifest_sha256,
            "expected_manifest_sha256",
        )
        if file_sha256(path / MANIFEST_NAME) != expected_manifest_sha256:
            raise ValueError("Manifest differs from the active selection")
    database = path / DATABASE_NAME
    # Compare with our code-owned schema; never run SQL from package metadata.
    with duckdb.connect(":memory:") as expected:
        expected.execute(SCHEMA_SQL)
        signature = _schema_signature(expected)
    with duckdb.connect(
        str(database),
        read_only=True,
        config=READ_CONFIG,
    ) as connection:
        connection.execute("SET TimeZone = 'UTC'")
        if _schema_signature(connection) != signature:
            raise ValueError("Database schema differs from voc-data-state-v1")
        warnings = validate_database(connection)
        row = connection.execute(
            "SELECT publication_id, dataset_id, schema_version, created_at FROM publication"
        ).fetchone()
        if row[:3] != tuple(
            manifest[key]
            for key in (
                "publication_id",
                "dataset_id",
                "schema_version",
            )
        ) or row[3] != utc_datetime(
            manifest["created_at"],
            "created_at",
        ):
            raise ValueError("Manifest identity differs from the database publication")
        yield OpenPublication(
            connection,
            manifest,
            warnings,
        )


def validate_bundle(path):
    """Check a bundle without installing it or changing any of its files."""
    with _open_bundle(path) as opened:
        return opened.manifest


def _publication_id(value):
    # A canonical UUID also prevents traversal or caller-selected filesystem paths.
    try:
        valid = (
            isinstance(
                value,
                str,
            )
            and str(UUID(value)) == value
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("publication_id must be a canonical UUID")
    return value


def _digest(value, label):
    if not isinstance(
        value,
        str,
    ) or not re.fullmatch(
        r"[0-9a-f]{64}",
        value,
    ):
        raise ValueError(f"{label} must be a SHA-256 hex digest")


def _local_directory(value):
    # Paths are operator configuration, never something read from a manifest.
    if re.match(
        r"^[A-Za-z][A-Za-z0-9+.-]*:",
        os.fspath(value),
    ):
        raise ValueError("Storage must be a local directory, not a URI")
    return Path(value).expanduser().resolve()


def current_publication(state):
    """Read the active pointer without creating directories or opening a catalog.

    This validates pointer metadata only. Use open_publication with the pointer's
    manifest_sha256 to verify that the selected catalog entry is still available.
    """
    path = _local_directory(state) / "active.json"
    if path.is_symlink():
        raise ValueError("Active selection must not be a symlink")
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_MANIFEST_BYTES + 1)
    except FileNotFoundError as error:
        raise LookupError("No active publication") from error
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("Active selection exceeds the size limit")
    pointer = json.loads(
        payload,
        object_pairs_hook=_json_object,
    )
    _fields(
        pointer,
        ("publication_id", "manifest_sha256", "activated_at"),
        "active selection",
    )
    _publication_id(pointer["publication_id"])
    _digest(
        pointer["manifest_sha256"],
        "manifest_sha256",
    )
    pointer["activated_at"] = (
        utc_datetime(
            pointer["activated_at"],
            "activated_at",
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )
    return pointer


@contextmanager
def open_publication(catalog, publication_id, *, expected_manifest_sha256=None):
    """Open one installed UUID as validated, restricted, read-only DuckDB data.

    Yield an OpenPublication with connection, manifest and warnings. Missing IDs
    and invalid input never create files. The connection closes on every exit.
    """
    publication_id = _publication_id(publication_id)
    path = _local_directory(catalog) / publication_id
    if path.is_symlink():
        raise ValueError("Installed publication must not be a symlink")
    if not path.exists():
        raise LookupError("Publication is not installed")
    with _open_bundle(
        path,
        expected_manifest_sha256=expected_manifest_sha256,
    ) as opened:
        if opened.manifest["publication_id"] != publication_id:
            raise ValueError("Catalog directory and publication identity differ")
        yield opened


@contextmanager
def _activation_lock(catalog, state):
    # Lock both resources: distinct catalogs may share state, or vice versa.
    with ExitStack() as stack:
        for root in sorted(
            {catalog, state},
            key=str,
        ):
            stream = stack.enter_context(
                (root / ".publication-activation.lock").open("a+b")
            )
            try:
                fcntl.flock(
                    stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError as error:
                raise ValueError(
                    "Another publication activation is in progress"
                ) from error
        yield
    # Closing the streams releases the locks, even after an exception.


def _sync_directory(path):
    # Persist directory entries after publishing a package or replacing a pointer.
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_active(state, pointer):
    temporary = None
    try:
        # Keep the old pointer readable until a complete new JSON file is durable.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".active-",
            suffix=".json",
            dir=state,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(
                stream.fileno(),
                0o644,
            )
            json.dump(
                pointer,
                stream,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(state / "active.json")
        _sync_directory(state)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def activate_bundle(bundle, catalog, state):
    """Install a validated copy, then atomically select it as the active version.

    Existing UUIDs must have identical file bytes. Repeating the active selection
    preserves its activation timestamp. Failures before pointer replacement leave
    the previous selection intact; a complete inactive package may remain for retry.
    """
    # Reject invalid inputs before allocating any catalog or state directories.
    bundle = Path(bundle).expanduser()
    manifest = validate_bundle(bundle)
    source_digest = file_sha256(bundle / MANIFEST_NAME)
    catalog, state = _local_directory(catalog), _local_directory(state)
    source = bundle.resolve()
    if catalog.is_relative_to(state) or state.is_relative_to(catalog):
        raise ValueError("Catalog and state must be separate, non-nested directories")
    if catalog.is_relative_to(source) or state.is_relative_to(source):
        raise ValueError("Catalog and state must not be created inside a source bundle")
    catalog.mkdir(
        parents=True,
        exist_ok=True,
    )
    state.mkdir(
        parents=True,
        exist_ok=True,
    )

    with _activation_lock(
        catalog,
        state,
    ):
        try:
            previous = current_publication(state)
        except LookupError:
            previous = None
        staging = Path(
            tempfile.mkdtemp(
                prefix=".install-",
                dir=catalog,
            )
        )
        try:
            # Copy only the two contract files, then validate the independent copy.
            for name in (DATABASE_NAME, MANIFEST_NAME):
                target = staging / name
                shutil.copyfile(
                    bundle / name,
                    target,
                    follow_symlinks=False,
                )
                target.chmod(0o644)
                with target.open("rb") as stream:
                    os.fsync(stream.fileno())
            copied = validate_bundle(staging)
            digest = file_sha256(staging / MANIFEST_NAME)
            if copied != manifest or digest != source_digest:
                raise ValueError("Source bundle changed while it was being installed")
            # The future application can read these copies without running as root.
            staging.chmod(0o755)
            _sync_directory(staging)
            identifier = copied["publication_id"]
            destination = catalog / identifier

            # Reuse byte-identical packages; a UUID never acquires different content.
            if destination.exists() or destination.is_symlink():
                with open_publication(
                    catalog,
                    identifier,
                ) as installed:
                    if (
                        installed.manifest != copied
                        or file_sha256(
                            destination / MANIFEST_NAME,
                        )
                        != digest
                    ):
                        raise ValueError(
                            "Publication ID already exists with different bytes"
                        )
            else:
                staging.rename(destination)
            _sync_directory(catalog)

            # Idempotent retries keep their original activation time.
            if previous is not None and (
                previous["publication_id"] == identifier
                and previous["manifest_sha256"] == digest
            ):
                return previous
            pointer = {
                "publication_id": identifier,
                "manifest_sha256": digest,
                "activated_at": datetime.now(UTC)
                .isoformat()
                .replace(
                    "+00:00",
                    "Z",
                ),
            }
            _replace_active(
                state,
                pointer,
            )
            return pointer
        finally:
            # An installed directory is immutable; cleanup concerns only our staging.
            if staging.exists():
                shutil.rmtree(staging)
