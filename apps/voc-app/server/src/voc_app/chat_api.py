"""Expose conversation services; the same services can later support MCP."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .agent.contracts import AgentError, Ask
from .auth import Identity, current_user, require_same_origin
from .conversations import ConversationError
from .data_api import failure, reject_query_parameters

router = APIRouter(prefix="/api/v1", tags=["Conversations"])
Caller = Annotated[Identity, Depends(current_user)]


def service(request):
    conversations = request.app.state.conversations
    if conversations is None:
        raise failure(
            503,
            "chat_unavailable",
            "El almacenamiento del chat no está disponible. El dashboard sigue disponible.",
        )
    return conversations


@router.post("/ask")
async def ask(payload: Ask, request: Request, caller: Caller):
    reject_query_parameters(request)
    # Prevent another website from spending model calls through a signed-in browser.
    require_same_origin(request, caller, "Envía preguntas desde la aplicación VOC.")
    try:
        conversation = await service(request).ask(payload)
        turn = next(
            turn
            for turn in conversation["turns"]
            if turn["request_id"] == payload.request_id
        )
        return JSONResponse(
            conversation, status_code=202 if turn["status"] == "running" else 200
        )
    except ConversationError as error:
        raise failure(error.status, error.code, error.message) from error
    except AgentError as error:
        raise failure(503, error.code, error.message) from error


@router.get("/conversations/{conversation_id}")
def read_conversation(conversation_id: str, request: Request, caller: Caller):
    reject_query_parameters(request)
    try:
        if str(UUID(conversation_id)) != conversation_id:
            raise ValueError("Noncanonical UUID")
    except ValueError as error:
        raise failure(
            422,
            "invalid_conversation_id",
            "Usa un identificador de conversación válido.",
        ) from error
    try:
        return service(request).read_conversation(conversation_id)
    except ConversationError as error:
        raise failure(error.status, error.code, error.message) from error
