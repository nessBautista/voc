"""Translate HTTP reads into the shared publication library's operations."""

from uuid import UUID

import duckdb
from fastapi import APIRouter, Depends, Request
from voc.publications import queries, store
from voc.publications import selection as selection_fields

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
# half-read. Version 2 adds one analysis candidate to the dataset description.
SUPPORTED_SCHEMA_VERSIONS = ("voc-data-state-v1", "voc-data-state-v2")


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


def read_selection(request):
    """The query string as a selection mapping: known fields only, lists split on commas."""
    selection = {}
    for name, value in request.query_params.multi_items():
        if name not in selection_fields.FIELDS or name in selection:
            raise failure(
                422,
                "invalid_request",
                "Unknown or repeated selection parameter: " + name[:40],
            )
        selection[name] = (
            value.split(",")
            if name in ("platforms", "labels", "assessments")
            else value
        )
    return selection or None


@router.get("/{publication_id}/{kind}")
def publication_query(publication_id: str, kind: str, request: Request):
    """Read a fixed publication; activation changes never redirect this query."""
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
    if kind not in queries.KINDS:
        raise failure(
            422,
            "capability_unavailable",
            "Use one of: " + ", ".join(queries.KINDS) + ".",
        )
    # Dataset kinds take no filters; analysis kinds take the one shared selection.
    if kind in selection_fields.ANALYSIS_KINDS:
        selection = read_selection(request)
    else:
        reject_query_parameters(request)
        selection = None

    try:
        # Preserve the complete library response, including warnings and result ID.
        return queries.query(
            request.app.state.settings.publications_dir,
            publication_id,
            kind,
            selection,
        )
    except selection_fields.SelectionError as error:
        raise failure(422, "invalid_selection", str(error)) from error
    except LookupError as error:
        raise failure(
            404,
            "publication_not_found",
            "This publication is not installed.",
        ) from error
    except queries.CapabilityUnavailable as error:
        # A v1 package asked for a v2 kind is a caller error, not a broken package.
        raise failure(
            422,
            "capability_unavailable",
            "This publication does not offer that capability.",
        ) from error
    except (ValueError, OSError, duckdb.Error) as error:
        raise failure(
            503,
            "publication_unavailable",
            "This publication cannot be read or validated.",
        ) from error
