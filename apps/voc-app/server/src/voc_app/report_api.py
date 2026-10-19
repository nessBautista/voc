"""Create and retrieve frozen conversation reports without invoking the agent."""

from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import field_validator

from .agent.contracts import Contract
from .conversations import ConversationError
from .data_api import failure, reject_query_parameters

router = APIRouter(prefix="/api/v1", tags=["Reports"])


def identity(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError("Noncanonical UUID")
    except ValueError as error:
        raise failure(
            422, "invalid_report_identity", "Usa un identificador UUID válido."
        ) from error
    return value


class PrepareReport(Contract):
    request_id: str
    publication_id: str

    @field_validator("request_id", "publication_id")
    @classmethod
    def canonical_id(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Use a canonical UUID")
        return value


def service(request):
    reports = request.app.state.reports
    if reports is None:
        raise failure(
            503,
            "reports_unavailable",
            "El almacenamiento de informes no está disponible.",
        )
    return reports


@router.post("/conversations/{conversation_id}/reports")
def prepare(conversation_id: str, payload: PrepareReport, request: Request):
    reject_query_parameters(request)
    # Match the conversation API's local-origin boundary for state-changing requests.
    origin = request.headers.get("origin")
    if origin and (
        urlsplit(origin).scheme not in ("http", "https")
        or urlsplit(origin).netloc != request.headers.get("host")
    ):
        raise failure(
            403, "origin_not_allowed", "Prepara el informe desde la aplicación VOC."
        )
    try:
        return service(request).prepare(
            identity(conversation_id), payload.request_id, payload.publication_id
        )
    except ConversationError as error:
        raise failure(error.status, error.code, error.message) from error


@router.get("/reports/{report_id}")
def read(report_id: str, request: Request):
    reject_query_parameters(request)
    try:
        return service(request).read(identity(report_id))
    except ConversationError as error:
        raise failure(error.status, error.code, error.message) from error


@router.get("/reports/{report_id}/chunks")
def chunk(report_id: str, request: Request, offset: int = Query(default=0, ge=0)):
    if (
        set(request.query_params) - {"offset"}
        or len(request.query_params.getlist("offset")) > 1
    ):
        raise failure(422, "invalid_request", "Solo se admite una posición offset.")
    try:
        return service(request).chunk(identity(report_id), offset)
    except ConversationError as error:
        raise failure(error.status, error.code, error.message) from error
