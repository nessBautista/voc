"""Read-only bundle validation; activation and catalog queries come later."""

import hashlib
import json
import re
from pathlib import Path

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


def validate_bundle(path):
    """Check manifest, closed-file integrity, schema and persisted row contents.

    Returns the validated manifest. This does not install or activate a package.
    Hashes detect changed bytes; permissions on the package establish trust.
    """
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
        validate_database(connection)
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
    return manifest
