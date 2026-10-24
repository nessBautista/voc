"""Offer the published-data reader and shared conversations through MCP."""

import asyncio
import json
import logging
import secrets
from typing import Literal
from uuid import UUID

import duckdb
from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import ValidationError, field_validator
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from voc.publications import queries
from voc.publications.queries import CapabilityUnavailable
from voc.publications.selection import SelectionError

from ..agent.contracts import AgentError, Ask, Contract
from ..auth import LOCAL_DEVELOPER
from ..conversations import ConversationError
from ..data_api import read_current

logger = logging.getLogger(__name__)
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class GetDataState(Contract):
    kind: Literal[
        "overview",
        "sources",
        "coverage",
        "freshness",
        "embeddings",
        "summary",
        "topic_list",
        "timeline",
        "platforms",
        "topic_detail",
        "reviews",
    ]
    publication_id: str | None = None
    # The analysis selection, validated and canonicalized by the library on read.
    platforms: list[str] | None = None
    labels: list[str] | None = None
    assessments: list[str] | None = None
    month_from: str | None = None
    month_to: str | None = None
    search: str | None = None
    topic_id: int | None = None
    sort: str | None = None
    page: int | None = None
    page_size: int | None = None

    def selection(self):
        fields = self.model_dump(exclude={"kind", "publication_id"})
        return {key: value for key, value in fields.items() if value is not None}

    @field_validator("publication_id")
    @classmethod
    def canonical_id(cls, value):
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Use a canonical publication UUID")
        return value


def input_schema(model):
    """Publish UUID restrictions as well as enforcing them at execution."""
    schema = model.model_json_schema()
    for name, value in schema["properties"].items():
        if name.endswith("_id"):
            for option in value.get("anyOf", [value]):
                if option.get("type") == "string":
                    option["pattern"] = UUID_PATTERN
    return schema


def tool_result(message, *, error=None, **data):
    """Keep evidence available to both structured and text-only clients."""
    envelope = {
        "schema_version": "voc-mcp-v1",
        "ok": error is None,
        "message": message,
        **data,
    }
    if error is not None:
        envelope["error"] = error
    return CallToolResult(
        is_error=error is not None,
        structured_content=envelope,
        content=[
            TextContent(
                type="text",
                text=message + "\n\n" + json.dumps(envelope, ensure_ascii=False),
            )
        ],
    )


def read_data(settings, request):
    """Capture the active identity once, then read that immutable publication."""
    identity = request.publication_id
    if identity is None:
        identity = read_current(settings)["publication_id"]
    return queries.query(
        settings.publications_dir, identity, request.kind, request.selection() or None
    )


class AccessGuard:
    """Require a development bearer token before touching the MCP transport."""

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        request = Request(scope)
        if not self.settings.mcp_enabled:
            response = JSONResponse(
                {"error": {"code": "mcp_disabled", "message": "MCP is disabled."}},
                status_code=404,
            )
        elif request.query_params:
            response = JSONResponse(
                {"error": "query_parameters_not_allowed"}, status_code=400
            )
        elif any(
            len(request.headers.getlist(name)) > 1
            for name in ("authorization", "host", "origin")
        ):
            response = JSONResponse({"error": "ambiguous_headers"}, status_code=400)
        else:
            # Compare bytes so malformed non-ASCII headers cannot raise TypeError.
            supplied = request.headers.get("authorization", "").split(" ", 1)
            valid = (
                len(supplied) == 2
                and supplied[0].lower() == "bearer"
                and secrets.compare_digest(
                    supplied[1].encode(), self.settings.mcp_token.encode()
                )
            )
            if valid:
                await self.app(scope, receive, send)
                return
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="voc"'},
            )
        response.headers["Cache-Control"] = "no-store"
        await response(scope, receive, send)


def mount_mcp(application, settings):
    """Register one endpoint; the caller owns its session-manager lifespan."""
    tools = [
        Tool(
            name="voc_get_data_state",
            description=(
                "Read published dataset metadata, counts and analysis. kind is overview, sources, "
                "coverage, freshness or embeddings for the data state, or summary, topic_list, "
                "timeline, platforms, topic_detail or reviews for the analysis of a package that "
                "carries topics and sentiment. Analysis kinds take an optional selection: platforms, "
                "labels, assessments, month_from, month_to (YYYY-MM), search, and per kind topic_id, "
                "sort (volume, negative, positive), page and page_size. Omit publication_id to "
                "resolve the active publication once; retain its returned ID for consistent "
                "follow-up reads. No SQL or processing is available."
            ),
            input_schema=input_schema(GetDataState),
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        ),
        Tool(
            name="voc_ask",
            description=(
                "Ask the VOC agent about published dataset state. Saves a conversation shared "
                "with the web. Generate a new canonical UUID request_id for each NEW question. "
                "Omit conversation_id on the first question; use its returned ID for follow-ups. "
                "If status is running, wait briefly and repeat this tool with the EXACT original "
                "arguments, including omitted conversation_id, to poll without another model call. "
                "Never substitute a new request_id for a retry. completed, failed and interrupted "
                "are terminal. Publication is pinned for the conversation. Cannot run pipelines "
                "or open the Mac app. mode is daily (default, one plan) or research (a bounded chain of "
                "up to six queries with a model assessment between steps). Model mode may send "
                "question/history and step summaries to its configured provider."
            ),
            input_schema=input_schema(Ask),
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=True,
            ),
        ),
    ]

    async def list_tools(context, params):
        return ListToolsResult(tools=tools)

    async def call_tool(context, params):
        try:
            if params.name == "voc_get_data_state":
                request = GetDataState.model_validate(params.arguments or {})
                # DuckDB and file validation must not block accepted chat tasks.
                result = await asyncio.to_thread(read_data, settings, request)
                return tool_result(
                    "Published data; retain publication_id and result_id with its warnings.",
                    result=result,
                )
            if params.name != "voc_ask":
                return tool_result("Unknown tool.", error={"code": "unknown_tool"})
            request = Ask.model_validate(params.arguments or {})
            service = application.state.conversations
            if service is None:
                raise AgentError(
                    "chat_unavailable", "Conversation storage is unavailable."
                )
            # MCP is a local-mode transport; settings refuse it in cognito mode.
            conversation = await service.ask(request, LOCAL_DEVELOPER)
            turn = next(
                item
                for item in conversation["turns"]
                if item["request_id"] == request.request_id
            )
            status = turn["status"]
            if status == "completed":
                message = turn["answer"]["text"]
            elif status == "running":
                message = "Running. Wait briefly, then repeat voc_ask with the exact original arguments."
            else:
                message = f"Turn {status}. Retrying this request does not restart it."
            return tool_result(
                message,
                error=turn["error"] if status in ("failed", "interrupted") else None,
                request_id=request.request_id,
                status=status,
                conversation=conversation,
            )
        except ValidationError:
            return tool_result(
                "Invalid tool arguments. Consult tools/list.",
                error={"code": "invalid_arguments"},
            )
        except HTTPException as error:
            return tool_result(error.detail["message"], error=error.detail)
        except (ConversationError, AgentError) as error:
            return tool_result(
                error.message, error={"code": error.code, "message": error.message}
            )
        except LookupError:
            return tool_result(
                "This publication is not installed, or the topic is not in it.",
                error={"code": "publication_not_found"},
            )
        except CapabilityUnavailable:
            return tool_result(
                "This publication does not carry that kind; read the data-state kinds instead.",
                error={"code": "capability_unavailable"},
            )
        except SelectionError as error:
            return tool_result(
                "The selection is not valid for this kind: " + str(error),
                error={"code": "invalid_selection"},
            )
        except (ValueError, OSError, duckdb.Error):
            return tool_result(
                "Published data could not be read or validated.",
                error={"code": "publication_unavailable"},
            )
        except Exception:  # noqa: BLE001 — sanitize the external protocol boundary
            # Protocol errors must not contain private paths or provider payloads.
            logger.error(
                "MCP operation failed; inspect application storage and configuration."
            )
            return tool_result(
                "The operation could not be completed.",
                error={"code": "internal_error"},
            )

    server = Server(
        "voc",
        version="0.1.0",
        instructions="Read published data or ask bounded questions. Retain IDs and warnings. Never claim running work is complete.",
        get_tool_input_schema=lambda name: next(
            (tool.input_schema for tool in tools if tool.name == name), None
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    transport = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=16_384,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.mcp_allowed_hosts),
            allowed_origins=list(settings.mcp_allowed_origins),
        ),
    )
    # Exact ASGI routes avoid a trailing-slash redirect and leave the web reachable.
    guard = AccessGuard(transport, settings)
    application.router.routes.append(Route("/mcp", endpoint=guard))
    return server
