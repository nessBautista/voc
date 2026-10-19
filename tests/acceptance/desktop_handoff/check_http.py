"""Check the optional Mac listener from a container using the official MCP Python SDK.

Run with the VOC server development environment (mcp 2.3). The default action
only checks authentication/discovery. `open` is an explicit native-window action.
Credentials are read privately; this script never edits a harness configuration.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def identity(value):
    """Require the same canonical identity that the native dispatcher accepts."""
    if str(UUID(value)) != value:
        raise argparse.ArgumentTypeError("Use a canonical lowercase UUID.")
    return value


def credential(path):
    """Read a private file or the caller's environment without exposing its value."""
    if path:
        if path.stat().st_mode & 0o077:
            raise ValueError("The token file must be private (chmod 600).")
        token = path.read_text().strip()
    else:
        token = os.environ.get("VOC_DESKTOP_MCP_TOKEN", "")
    if not 32 <= len(token) <= 512 or not all(33 <= ord(char) <= 126 for char in token):
        raise ValueError("Supply a valid private VOC_DESKTOP_MCP_TOKEN or --token-file.")
    return token


async def check(args):
    """Check the edge first, then invoke the shared tool contract through the SDK."""
    parts = urlsplit(args.url)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}
        or parts.port != 8766
        or parts.path != "/mcp"
        or parts.query or parts.fragment or parts.username or parts.password
    ):
        raise ValueError("Use the local development listener on port 8766 at /mcp.")
    token = credential(args.token_file)
    async with httpx2.AsyncClient(trust_env=False, timeout=20) as http:
        for method in ("POST", "GET", "DELETE"):
            response = await http.request(method, args.url)
            if response.status_code != 401:
                raise ValueError("Unauthenticated request was not rejected.")
        response = await http.post(args.url, headers={"Authorization": "Bearer wrong"})
        if response.status_code != 401:
            raise ValueError("Wrong credential was not rejected.")
        headers = {"Authorization": "Bearer " + token}
        for invalid in ({"Origin": "https://example.invalid"}, {"Host": "example.invalid:8766"}):
            response = await http.post(args.url, headers=headers | invalid)
            if response.status_code != 403:
                raise ValueError("Host/Origin rejection failed.")

    async with (
        httpx2.AsyncClient(headers=headers, trust_env=False, timeout=20) as http,
        streamable_http_client(args.url, http_client=http) as streams,
        ClientSession(*streams, read_timeout_seconds=25) as session,
    ):
        initialized = await session.initialize()
        names = [tool.name for tool in (await session.list_tools()).tools]
        if names != ["voc_open", "voc_read_result"]:
            raise ValueError("Unexpected desktop tool catalog.")
        result = {
            "transport": "SDK Streamable HTTP",
            "version": initialized.server_info.version,
            "edge_checks": "passed",
            "tools": names,
        }
        if args.action == "open":
            if not args.request_id or not args.question:
                raise ValueError("open requires --request-id and --question.")
            called = await session.call_tool("voc_open", {"request_id": args.request_id, "question": args.question})
            result["work"] = called.structured_content
            result["tool_error"] = called.is_error
        elif args.action == "read":
            if not args.work_id:
                raise ValueError("read requires --work-id.")
            called = await session.call_tool("voc_read_result", {"work_id": args.work_id})
            result["work"] = called.structured_content
            result["tool_error"] = called.is_error
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "open", "read"], nargs="?", default="check")
    parser.add_argument("--url", default="http://host.docker.internal:8766/mcp")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--request-id", type=identity)
    parser.add_argument("--question")
    parser.add_argument("--work-id", type=identity)
    args = parser.parse_args()
    try:
        result = asyncio.run(check(args))
    except Exception:  # noqa: BLE001 -- Transport failures can embed request details.
        # Client exceptions can include request details. Keep this runner's diagnostic private-safe.
        parser.exit(1, "Desktop HTTP check failed. Check the listener, token, port and arguments; no credential was printed.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("tool_error"):
        parser.exit(1)


if __name__ == "__main__":
    main()
