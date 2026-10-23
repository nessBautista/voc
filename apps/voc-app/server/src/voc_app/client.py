"""Read publications through the shared library; no HTTP hop and no trusted caller."""

import asyncio
from time import monotonic
from uuid import UUID

import duckdb
from starlette.exceptions import HTTPException
from voc.publications import queries

from .agent.contracts import KINDS, AgentError, validate_result
from .data_api import SUPPORTED_SCHEMA_VERSIONS, read_current


class PublicationService:
    def __init__(self, settings):
        self.settings = settings

    async def current(self):
        try:
            # The same validation the HTTP route performs, without the route.
            selected = await asyncio.to_thread(read_current, self.settings)
            identity = selected["publication_id"]
            if (
                str(UUID(identity)) != identity
                or selected["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS
            ):
                raise ValueError("Invalid selection")
            return identity
        except HTTPException as error:
            raise AgentError(
                "data_unavailable",
                "No pudimos consultar la publicación. Intenta de nuevo cuando esté disponible.",
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise AgentError(
                "data_unavailable", "La selección publicada no es válida."
            ) from error

    async def query(self, publication_id, kind, trace):
        if (
            kind not in KINDS
            or str(UUID(publication_id)) != publication_id
            or len(trace) >= 3
        ):
            raise ValueError("Query outside the permitted budget")
        started = monotonic()
        try:
            # DuckDB reads block; keep the event loop free for other requests.
            result = await asyncio.to_thread(
                queries.query, self.settings.publications_dir, publication_id, kind
            )
        except (LookupError, ValueError, OSError, duckdb.Error) as error:
            raise AgentError(
                "data_unavailable", "No pudimos validar los datos publicados."
            ) from error
        validate_result(result, publication_id, kind)
        trace.append(
            {
                "operation": kind,
                "publication_id": publication_id,
                "result_id": result["result_id"],
                "duration_ms": round((monotonic() - started) * 1000),
            }
        )
        return result
