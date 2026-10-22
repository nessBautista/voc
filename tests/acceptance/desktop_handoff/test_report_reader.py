"""Exercise the independent verifier before trusting a live MCP report digest."""

import base64
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

# The verifier runs inside the app container, whose HTTP client is httpx2; the
# Operator workspace collects this file too and must skip it rather than fail.
pytest.importorskip(
    "httpx2", reason="check_report runs where the app's httpx2 is installed"
)
sys.path.insert(0, str(Path(__file__).parent))
from check_report import CHUNK_BYTES, Download


def fixture():
    work = str(uuid4())
    data = ("x" * (CHUNK_BYTES - 1) + "🙂 café漢字\n" * 4000).encode()
    report = {
        "schema_version": "voc-report-v1",
        "media_type": "text/markdown; charset=utf-8",
        "report_id": str(uuid4()),
        "conversation_id": str(uuid4()),
        "publication_id": str(uuid4()),
        "byte_length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "snapshot_sha256": "a" * 64,
    }
    pages = []
    for offset in range(0, len(data), CHUNK_BYTES):
        raw = data[offset : offset + CHUNK_BYTES]
        pages.append(
            {
                "ok": True,
                "work_id": work,
                "result_status": "ready",
                "report": report,
                "conversation_id": report["conversation_id"],
                "publication_id": report["publication_id"],
                "chunk": {
                    "encoding": "base64",
                    "data": base64.b64encode(raw).decode(),
                    "offset": offset,
                    "next_offset": offset + len(raw),
                    "eof": offset + len(raw) == len(data),
                },
            }
        )
    return work, data, pages


def test_exact_digest_unicode_and_pinned_next_page():
    work, data, pages = fixture()
    reader = Download(work)
    assert reader.arguments() == {"work_id": work}
    for page in pages:
        reader.accept(page)
        assert reader.arguments()["report_id"] == page["report"]["report_id"]
        assert reader.arguments()["offset"] == page["chunk"]["next_offset"]
    assert reader.finish()["verified_sha256"] == hashlib.sha256(data).hexdigest()
    assert bytes(reader.data) == data


@pytest.mark.parametrize(
    "change", ["report", "position", "eof", "digest", "work", "base64"]
)
def test_incomplete_altered_or_mixed_downloads_are_rejected(change):
    work, _, pages = fixture()
    reader = Download(work)
    with pytest.raises(ValueError):
        reader.finish()
    if change == "report":
        reader.accept(pages[0])
        pages = [json.loads(json.dumps(pages[1]))]
        pages[0]["report"]["report_id"] = str(uuid4())
    elif change == "position":
        pages[0]["chunk"]["offset"] = 1
    elif change == "eof":
        pages[0]["chunk"]["eof"] = True
    elif change == "digest":
        for page in pages:
            page["report"]["sha256"] = "b" * 64
    elif change == "work":
        pages[0]["work_id"] = str(uuid4())
    else:
        pages[0]["chunk"]["data"] = "invalid base64!"
    with pytest.raises(ValueError):
        for page in pages:
            reader.accept(page)
        reader.finish()
