"""Read the fixed internal API; model output never supplies URLs or SQL."""

import json
from time import monotonic
from uuid import UUID

import httpx

from .agent.contracts import KINDS, AgentError, validate_result


class PublicationClient:
    def __init__(self, client):
        self.client = client

    async def read(self, path):
        try:
            # Refuse redirects and limit bytes before decoding any response.
            async with self.client.stream("GET", path) as response:
                if response.status_code != 200:
                    raise AgentError(
                        "data_unavailable",
                        "No pudimos consultar la publicación. Intenta de nuevo cuando esté disponible.",
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_000_000:
                        raise ValueError("API response exceeds the limit")

                return json.loads(body)
        except (httpx.HTTPError, ValueError) as error:
            raise AgentError(
                "data_unavailable", "No pudimos validar los datos publicados."
            ) from error

    async def current(self):
        result = await self.read("/api/v1/publications/current")
        try:
            identity = result["publication_id"]
            if (
                str(UUID(identity)) != identity
                or result["schema_version"] != "voc-data-state-v1"
            ):
                raise ValueError("Invalid selection")
            return identity
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
        result = await self.read(f"/api/v1/publications/{publication_id}/{kind}")
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
