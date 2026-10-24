"""Exercise authenticated MCP over real HTTP with the shared conversation service."""

import asyncio
import json
import socket
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from uuid import uuid4

import httpx
import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import Client, ClientSession
from mcp.client.streamable_http import streamable_http_client
from test_chat import CountPlanner, app_for, payload
from voc_app.agent.contracts import validate_result
from voc_app.main import create_app
from voc_app.settings import Settings

from voc.publications.store import activate_bundle

TOKEN = "test-mcp-token-not-a-real-credential-00001"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@contextmanager
def serve(settings):
    """A real MCP server on an ephemeral socket, so the SDK exercises the HTTP transport."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        selected = replace(
            settings,
            mcp_enabled=True,
            mcp_token=TOKEN,
            mcp_allowed_hosts=(origin.removeprefix("http://"),),
            mcp_allowed_origins=(origin,),
        )
        planner = CountPlanner(selected, delay=0.12)
        application = app_for(selected, planner)
        server = uvicorn.Server(uvicorn.Config(application, log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 8
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "MCP test server did not start"
        try:
            yield origin, application, planner
        finally:
            server.should_exit = True
            thread.join(timeout=8)
            assert not thread.is_alive(), "MCP test server did not stop"


@pytest.fixture
def mcp_app(settings, active):
    with serve(settings) as served:
        yield served


@asynccontextmanager
async def connect(origin):
    # SDK 2.x uses httpx2; the application's existing query client uses httpx.
    async with (
        httpx2.AsyncClient(headers=AUTH, trust_env=False, timeout=5) as http,
        streamable_http_client(origin + "/mcp", http_client=http) as streams,
        ClientSession(*streams, read_timeout_seconds=5) as session,
    ):
        initialized = await session.initialize()
        assert initialized.server_info.name == "voc"
        yield session


async def finish(session, request):
    for _ in range(100):
        result = await session.call_tool("voc_ask", request)
        body = result.structured_content
        if body.get("status") != "running":
            return result
        await asyncio.sleep(0.03)
    pytest.fail("MCP question did not finish")


def test_sdk_discovery_queries_and_cross_transport_follow_up(
    mcp_app, settings, active, bundles
):
    origin, _, planner = mcp_app

    async def exercise():
        async with connect(origin) as session:
            tools = (await session.list_tools()).tools
            assert {tool.name for tool in tools} == {"voc_ask", "voc_get_data_state"}
            assert all(
                tool.input_schema["additionalProperties"] is False for tool in tools
            )
            assert (
                next(
                    tool for tool in tools if tool.name == "voc_ask"
                ).annotations.read_only_hint
                is False
            )
            assert (
                next(
                    tool for tool in tools if tool.name == "voc_get_data_state"
                ).annotations.read_only_hint
                is True
            )
            for kind in ("overview", "sources", "coverage", "freshness", "embeddings"):
                result = await session.call_tool("voc_get_data_state", {"kind": kind})
                assert not result.is_error
                query = result.structured_content["result"]
                validate_result(query, active["publication_id"], kind)
                assert (
                    json.loads(result.content[0].text.split("\n\n", 1)[1])
                    == result.structured_content
                )
                assert (
                    await asyncio.to_thread(
                        httpx.get,
                        f"{origin}/api/v1/publications/{active['publication_id']}/{kind}",
                        trust_env=False,
                    )
                ).json() == query
            first = payload()
            result = await session.call_tool("voc_ask", first)
            assert result.structured_content["status"] == "running"
            result = await finish(session, first)
            conversation = result.structured_content["conversation"]
            identity = conversation["conversation_id"]
            assert (
                "8 registros de Google Play"
                in conversation["turns"][0]["answer"]["text"]
            )
            # The web app observes the very same saved conversation and evidence.
            assert (
                await asyncio.to_thread(
                    httpx.get,
                    f"{origin}/api/v1/conversations/{identity}",
                    trust_env=False,
                )
            ).json() == conversation
            activate_bundle(bundles[1], settings.publications_dir, settings.state_dir)
            follow = payload("¿y de App Store?", identity)
            assert (
                await asyncio.to_thread(
                    httpx.post, origin + "/api/v1/ask", json=follow, trust_env=False
                )
            ).status_code == 202
            result = await finish(session, follow)
            assert not result.is_error
            assert "4 registros de App Store" in result.structured_content["message"]
            assert (
                result.structured_content["conversation"]["publication_id"]
                == active["publication_id"]
            )
            repeated = await session.call_tool("voc_ask", follow)
            assert repeated.structured_content == result.structured_content
            assert planner.calls == 2
            assert len(result.structured_content["conversation"]["turns"]) == 2
            new_active = await session.call_tool(
                "voc_get_data_state", {"kind": "overview"}
            )
            assert (
                new_active.structured_content["result"]["publication_id"]
                != active["publication_id"]
            )
            pinned = await session.call_tool(
                "voc_get_data_state",
                {"kind": "sources", "publication_id": active["publication_id"]},
            )
            assert (
                pinned.structured_content["result"]["publication_id"]
                == active["publication_id"]
            )
        # A fresh MCP connection can recover an old request without executing it.
        async with connect(origin) as session:
            retried = await session.call_tool("voc_ask", follow)
            assert retried.structured_content == result.structured_content
            assert planner.calls == 2

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "name,args,code",
    [
        ("voc_get_data_state", {"kind": "sentiment"}, "invalid_arguments"),
        (
            "voc_get_data_state",
            {"kind": "sources", "sql": "select 1"},
            "invalid_arguments",
        ),
        (
            "voc_get_data_state",
            {"kind": "sources", "publication_id": "../../secret"},
            "invalid_arguments",
        ),
        (
            "voc_get_data_state",
            {"kind": "sources", "publication_id": str(uuid4())},
            "publication_not_found",
        ),
        (
            "voc_ask",
            {"question": "Fuentes", "request_id": "bad-id"},
            "invalid_arguments",
        ),
        ("voc_ask", {**payload(), "limit": 10}, "invalid_arguments"),
        ("voc_ask", payload(" "), "invalid_arguments"),
        ("voc_ask", payload("a" * 1001), "invalid_arguments"),
        ("voc_ask", payload("Fuentes", str(uuid4())), "conversation_not_found"),
        ("run_pipeline", {}, "unknown_tool"),
    ],
)
def test_invalid_tool_calls_are_visible_and_start_no_work(mcp_app, name, args, code):
    origin, _, planner = mcp_app

    async def exercise():
        async with connect(origin) as session:
            result = await session.call_tool(name, args)
            assert result.is_error
            assert result.structured_content["error"]["code"] == code
            assert planner.calls == 0

    asyncio.run(exercise())


def test_conflicts_failed_turn_and_unsupported_scope(mcp_app):
    origin, _, planner = mcp_app

    async def exercise():
        async with connect(origin) as session:
            request = payload("Recalcula los embeddings")
            result = await finish(session, request)
            turn = result.structured_content["conversation"]["turns"][0]
            assert turn["answer"]["plan"]["intent"] == "unsupported"
            assert turn["answer"]["results"] == turn["answer"]["trace"] == []
            result = await session.call_tool(
                "voc_ask", {**request, "question": "Otra pregunta"}
            )
            assert result.is_error
            assert result.structured_content["error"]["code"] == "request_conflict"
            planner.delegate.settings = replace(
                planner.delegate.settings, planner_mode="model"
            )
            result = await finish(session, payload())
            assert result.is_error and result.structured_content["status"] == "failed"
            assert result.structured_content["error"]["code"] == "model_unavailable"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "headers,path,expected",
    [
        ({}, "/mcp", 401),
        ({"Authorization": "Bearer wrong"}, "/mcp", 401),
        ({**AUTH, "Host": "evil.example"}, "/mcp", 421),
        ({**AUTH, "Origin": "null"}, "/mcp", 403),
        ({**AUTH, "Origin": "https://evil.example"}, "/mcp", 403),
        (
            {**AUTH, "Host": "evil.example", "X-Forwarded-Host": "127.0.0.1:8091"},
            "/mcp",
            421,
        ),
        (AUTH, "/mcp?token=not-accepted", 400),
    ],
)
def test_access_is_checked_before_protocol_work(mcp_app, headers, path, expected):
    origin, _, planner = mcp_app
    response = httpx.post(origin + path, headers=headers, json={}, trust_env=False)
    assert response.status_code == expected
    assert response.headers["cache-control"] == "no-store"
    assert planner.calls == 0
    assert TOKEN not in response.text


def test_legacy_handshake_allowed_origin_and_body_limit(mcp_app):
    origin, _, _ = mcp_app
    headers = {
        **AUTH,
        "Origin": origin,
        "Accept": "application/json, text/event-stream",
    }
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "legacy-test", "version": "1"},
        },
    }
    with httpx.Client(base_url=origin, headers=headers, trust_env=False) as http:
        response = http.post("/mcp", json=message)
        assert response.status_code == 200
        assert response.json()["result"]["protocolVersion"] == "2025-11-25"
        response = http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": "2025-11-25"},
        )
        assert len(response.json()["result"]["tools"]) == 2
        assert (
            http.post(
                "/mcp",
                content=b" " * 20000,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        assert http.get("/health/ready").status_code == 200


@pytest.mark.parametrize(
    "token",
    ["", "short", "op://Development/voc-mcp-token/password", "a" * 32 + "\n", "é" * 32],
)
def test_enabled_mcp_requires_resolved_private_token(settings, token):
    with pytest.raises(ValueError, match="token"):
        replace(settings, mcp_enabled=True, mcp_token=token)


def test_disabled_mcp_never_falls_back_to_anonymous_access(settings, active):
    with TestClient(create_app(settings)) as client:
        assert client.post("/mcp", json={}).status_code == 404
        assert client.get("/health/ready").status_code == 200
    assert TOKEN not in repr(replace(settings, mcp_token=TOKEN))
    with pytest.raises(ValueError, match="token"):
        replace(settings, mcp_enabled=True, mcp_token=TOKEN, llm_api_key=TOKEN)


def test_mcp_environment_and_explicit_allowlists(settings, monkeypatch):
    monkeypatch.setenv("VOC_MCP_ENABLED", "true")
    monkeypatch.setenv("VOC_MCP_TOKEN", TOKEN)
    monkeypatch.setenv(
        "VOC_MCP_ALLOWED_HOSTS", "127.0.0.1:9000,host.docker.internal:9000"
    )
    monkeypatch.setenv("VOC_MCP_ALLOWED_ORIGINS", "http://127.0.0.1:9000")
    assert Settings.from_env().mcp_allowed_hosts == (
        "127.0.0.1:9000",
        "host.docker.internal:9000",
    )
    monkeypatch.setenv("VOC_MCP_ENABLED", "yes")
    with pytest.raises(ValueError, match="true or false"):
        Settings.from_env()
    with pytest.raises(ValueError, match="explicit"):
        replace(settings, mcp_allowed_hosts=("*",))


def test_closed_client_running_conflict_and_resume(mcp_app):
    origin, _, planner = mcp_app
    planner.delay = 0.4

    async def exercise():
        request = payload()
        async with connect(origin) as session:
            started = (await session.call_tool("voc_ask", request)).structured_content
            identity = started["conversation"]["conversation_id"]
            assert started["status"] == "running"
            busy = await session.call_tool("voc_ask", payload("Fuentes", identity))
            assert busy.is_error
            assert busy.structured_content["error"]["code"] == "conversation_busy"
        # Disconnecting the protocol client must not cancel the accepted question.
        await asyncio.sleep(0.5)
        async with connect(origin) as session:
            result = await finish(session, request)
            assert result.structured_content["status"] == "completed"
            assert len(result.structured_content["conversation"]["turns"]) == 1
            assert planner.calls == 1

    asyncio.run(exercise())


def test_selection_failure_and_unavailable_chat_are_tool_errors(mcp_app, settings):
    origin, application, _ = mcp_app
    (settings.state_dir / "active.json").unlink()

    async def exercise():
        async with connect(origin) as session:
            result = await session.call_tool("voc_get_data_state", {"kind": "overview"})
            assert result.is_error
            assert result.structured_content["error"]["code"] == "no_active_publication"
            application.state.conversations = None
            result = await session.call_tool("voc_ask", payload())
            assert result.is_error
            assert result.structured_content["error"]["code"] == "chat_unavailable"

    asyncio.run(exercise())


def test_duplicate_auth_and_host_headers_are_rejected(mcp_app):
    origin, _, _ = mcp_app
    for duplicate in ("Authorization", "Host", "Origin"):
        headers = [
            ("Authorization", AUTH["Authorization"]),
            ("Host", origin.removeprefix("http://")),
            ("Origin", origin),
        ]
        headers.append((duplicate, "invalid"))
        # ASGI transport keeps duplicate headers intact for this ambiguity check.
        _, application, _ = mcp_app

        async def request(headers=headers, application=application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url=origin
            ) as client:
                assert (
                    await client.post("/mcp", headers=headers, json={})
                ).status_code == 400

        asyncio.run(request())


def test_current_sdk_discovery_protocol(mcp_app):
    origin, _, _ = mcp_app

    async def exercise():
        async with (
            httpx2.AsyncClient(headers=AUTH, trust_env=False, timeout=5) as http,
            Client(
                streamable_http_client(origin + "/mcp", http_client=http),
                read_timeout_seconds=5,
            ) as client,
        ):
            assert {tool.name for tool in (await client.list_tools()).tools} == {
                "voc_get_data_state",
                "voc_ask",
            }
            result = await client.call_tool("voc_get_data_state", {"kind": "overview"})
            assert not result.is_error
            assert result.structured_content["ok"] is True

    asyncio.run(exercise())


def test_analysis_kinds_read_with_a_selection(settings, v2_bundle, bundles, tmp_path):
    from voc_app.settings import Settings

    v1_settings = Settings(
        publications_dir=tmp_path / "v1-catalog", state_dir=tmp_path / "v1-state"
    )
    activate_bundle(bundles[0], v1_settings.publications_dir, v1_settings.state_dir)
    v2_active = activate_bundle(
        v2_bundle, settings.publications_dir, settings.state_dir
    )

    async def exercise(origin_v2, origin_v1):
        async with connect(origin_v2) as session:
            tool = next(
                t
                for t in (await session.list_tools()).tools
                if t.name == "voc_get_data_state"
            )
            assert "topic_list" in tool.description
            assert "topic_id" in tool.input_schema["properties"]
            result = await session.call_tool(
                "voc_get_data_state", {"kind": "summary", "platforms": ["google_play"]}
            )
            assert not result.is_error, result.structured_content
            query = result.structured_content["result"]
            validate_result(
                query,
                v2_active["publication_id"],
                "summary",
                {"platforms": ["google_play"]},
            )
            assert query["selection"]["platforms"] == ["google_play"]
            assert query["data"]["reviews_in_view"] == 36
            paged = await session.call_tool(
                "voc_get_data_state",
                {"kind": "reviews", "topic_id": 0, "page_size": 3},
            )
            assert paged.structured_content["result"]["data"]["pages"] == 4
            for args, code in (
                (
                    {"kind": "overview", "platforms": ["google_play"]},
                    "invalid_selection",
                ),
                ({"kind": "topic_detail"}, "invalid_selection"),
                ({"kind": "topic_list", "sort": "random"}, "invalid_selection"),
                ({"kind": "topic_detail", "topic_id": 999}, "publication_not_found"),
            ):
                refused = await session.call_tool("voc_get_data_state", args)
                assert refused.is_error
                assert refused.structured_content["error"]["code"] == code, args
        async with connect(origin_v1) as session:
            refused = await session.call_tool("voc_get_data_state", {"kind": "summary"})
            assert (
                refused.structured_content["error"]["code"] == "capability_unavailable"
            )

    with serve(settings) as (origin_v2, _, _), serve(v1_settings) as (origin_v1, _, _):
        asyncio.run(exercise(origin_v2, origin_v1))
