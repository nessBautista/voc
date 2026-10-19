"""Retrieve a prepared report through Mac MCP and verify its bytes inside Argo.

No report is prepared and no window is opened. By default bytes stay in memory;
--output explicitly saves the verified Markdown to a new local file.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from check_http import credential, identity
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MAX_BYTES = 8 * 1024 * 1024
CHUNK_BYTES = 32768


class Download:
    def __init__(self, work_id, report_id=None):
        self.work_id = work_id
        self.report_id = report_id
        self.metadata = None
        self.data = bytearray()
        self.chunks = 0
        self.eof = False

    def arguments(self):
        arguments = {"work_id": self.work_id}
        if self.report_id:
            arguments.update(report_id=self.report_id, offset=len(self.data))
        return arguments

    def accept(self, payload):
        if (
            not payload.get("ok")
            or payload.get("result_status") != "ready"
            or payload.get("work_id") != self.work_id
        ):
            raise ValueError("The requested work has no readable prepared report.")
        report, chunk = payload["report"], payload["chunk"]
        if self.metadata is None:
            if (
                report["schema_version"] != "voc-report-v1"
                or report["media_type"] != "text/markdown; charset=utf-8"
                or type(report["byte_length"]) is not int
                or not 0 < report["byte_length"] <= MAX_BYTES
                or not re.fullmatch(r"[0-9a-f]{64}", report["sha256"])
                or not re.fullmatch(r"[0-9a-f]{64}", report["snapshot_sha256"])
            ):
                raise ValueError("Invalid report metadata.")
            for key in ("report_id", "conversation_id", "publication_id"):
                identity(report[key])
            if self.report_id and self.report_id != report["report_id"]:
                raise ValueError("A different report was returned.")
            self.report_id = report["report_id"]
            self.metadata = report
        if (
            report != self.metadata
            or payload["conversation_id"] != report["conversation_id"]
            or payload["publication_id"] != report["publication_id"]
        ):
            raise ValueError("The report identity changed during retrieval.")
        raw = base64.b64decode(chunk["data"], validate=True)
        position = len(self.data)
        end = position + len(raw)
        if (
            chunk["encoding"] != "base64"
            or type(chunk["offset"]) is not int
            or chunk["offset"] != position
            or type(chunk["next_offset"]) is not int
            or chunk["next_offset"] != end
            or len(raw) != min(CHUNK_BYTES, report["byte_length"] - position)
            or type(chunk["eof"]) is not bool
            or chunk["eof"] != (end == report["byte_length"])
        ):
            raise ValueError("Invalid report byte positions.")
        self.data.extend(raw)
        self.chunks += 1
        self.eof = chunk["eof"]

    def finish(self):
        if (
            not self.eof
            or self.metadata is None
            or len(self.data) != self.metadata["byte_length"]
        ):
            raise ValueError("Incomplete download.")
        digest = hashlib.sha256(self.data).hexdigest()
        if digest != self.metadata["sha256"]:
            raise ValueError("SHA-256 verification failed.")
        self.data.decode("utf-8", errors="strict")
        return {
            "work_id": self.work_id,
            "report": self.metadata,
            "chunks": self.chunks,
            "verified_bytes": len(self.data),
            "verified_sha256": digest,
            "utf8_verified": True,
        }


async def download(args):
    parts = urlsplit(args.url)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}
        or parts.port != 8766
        or parts.path != "/mcp"
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Use the local desktop listener at port 8766/mcp.")
    token = credential(args.token_file)
    reader = Download(args.work_id, args.report_id)
    async with asyncio.timeout(120):
        # Rotate before the listener's session limit; retain the pinned byte cursor.
        while not reader.eof:
            async with (
                httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + token},
                    trust_env=False,
                    timeout=20,
                ) as http,
                streamable_http_client(args.url, http_client=http) as streams,
                ClientSession(*streams, read_timeout_seconds=25) as session,
            ):
                await session.initialize()
                for _ in range(64):
                    result = await session.call_tool(
                        "voc_read_result", reader.arguments()
                    )
                    if result.is_error or not result.structured_content:
                        raise ValueError("Report read failed.")
                    reader.accept(result.structured_content)
                    if reader.eof:
                        break
    verified = reader.finish()
    if args.output:
        # Fail if the destination already exists; this probe never overwrites a report.
        with args.output.open("xb") as stream:
            stream.write(reader.data)
        verified["output"] = str(args.output)
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-id", required=True, type=identity)
    parser.add_argument("--report-id", type=identity)
    parser.add_argument("--url", default="http://host.docker.internal:8766/mcp")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(download(args))
    except Exception:  # noqa: BLE001 -- Keep credentials out of transport errors.
        # Transport exceptions may contain headers; never print their raw detail.
        parser.exit(
            1,
            "Report verification failed. Check prepared work, listener, credential and destination; no token was printed.\n",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
