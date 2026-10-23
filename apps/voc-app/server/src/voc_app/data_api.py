"""Translate HTTP reads into the shared publication library's operations."""

from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, Request
from voc.publications import queries, store

from .auth import current_user
from .errors import failure, reject_query_parameters

__all__ = ["failure", "read_current", "reject_query_parameters", "router"]

# Every publication read requires a recognised caller.
router = APIRouter(
    prefix="/api/v1/publications",
    tags=["Publications"],
    dependencies=[Depends(current_user)],
)


# The app serves the versions it understands; a newer package is refused, not
# half-read. The next increment teaches it voc-data-state-v2.
SUPPORTED_SCHEMA_VERSIONS = ("voc-data-state-v1",)


def read_current(settings):
    """Resolve the pointer once and validate the exact selected package."""
    try:
        pointer = store.current_publication(settings.state_dir)
    except LookupError as error:
        raise failure(
            503,
            "no_active_publication",
            "No publication is active. Activate a package from the Operator.",
        ) from error
    except (ValueError, OSError) as error:
        raise failure(
            503,
            "selection_unavailable",
            "The active selection cannot be read or validated.",
        ) from error

    try:
        # Use the captured identity and digest even if a new selection appears.
        with store.open_publication(
            settings.publications_dir,
            pointer["publication_id"],
            expected_manifest_sha256=pointer["manifest_sha256"],
        ) as opened:
            if opened.manifest["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
                raise ValueError("Unsupported publication schema for this server")
            return {
                **pointer,
                "schema_version": opened.manifest["schema_version"],
                "dataset_id": opened.manifest["dataset_id"],
                "capabilities": opened.manifest["capabilities"],
                "warnings": opened.warnings,
            }
    except (LookupError, ValueError, OSError, duckdb.Error) as error:
        raise failure(
            503,
            "publication_unavailable",
            "The selected publication is missing, unreadable or incompatible.",
        ) from error


@router.get("/current")
def current(request: Request):
    """Return the validated active selection and its supported capabilities."""
    reject_query_parameters(request)
    return read_current(request.app.state.settings)


@router.get("/{publication_id}/{kind}")
def publication_query(publication_id: str, kind: str, request: Request):
    """Read a fixed publication; activation changes never redirect this query."""
    reject_query_parameters(request)
    # Validate caller input separately from failures in the installed data.
    try:
        if str(UUID(publication_id)) != publication_id:
            raise ValueError("Noncanonical UUID")
    except ValueError as error:
        raise failure(
            422,
            "invalid_publication_id",
            "Use a canonical publication UUID.",
        ) from error
    if kind not in store.CAPABILITIES:
        raise failure(
            422,
            "capability_unavailable",
            "Use overview, sources, coverage, freshness or embeddings.",
        )

    try:
        # Preserve the complete library response, including warnings and result ID.
        return queries.query(
            request.app.state.settings.publications_dir,
            publication_id,
            kind,
        )
    except LookupError as error:
        raise failure(
            404,
            "publication_not_found",
            "This publication is not installed.",
        ) from error
    except (ValueError, OSError, duckdb.Error) as error:
        raise failure(
            503,
            "publication_unavailable",
            "This publication cannot be read or validated.",
        ) from error
