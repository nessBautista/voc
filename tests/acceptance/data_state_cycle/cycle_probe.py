"""Check the running acceptance app through CLI, HTTP and authenticated MCP.

Run inside the app image with stdin; it needs no checkout or test dependency.
Only its explicitly marked acceptance state may receive probe conversations.
"""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid5

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from voc_app.agent.contracts import validate_result

from voc.publications.queries import query
from voc.publications.store import current_publication, file_sha256

KINDS = ("overview", "sources", "coverage", "freshness", "embeddings")


def require(condition, message):
    """Keep checks active even when the interpreter runs with optimization."""
    if not condition:
        raise RuntimeError(message)


async def run(action):
    catalog = Path(os.environ["VOC_PUBLICATIONS_DIR"])
    state = Path(os.environ["VOC_APP_STATE_DIR"])
    info = json.loads((state / "cycle.json").read_text())
    require(info["format"] == "voc-app-cycle-v1", "Unrecognized acceptance state")
    require(
        os.environ.get("VOC_PLANNER_MODE") == "fixture",
        "This probe requires fixture mode",
    )
    require(not os.environ.get("VOC_LLM_API_KEY"), "Provider key must be absent")
    origin = os.environ.get("VOC_INTERNAL_API_URL", "http://127.0.0.1:8000")
    evidence_file = state / "cycle-evidence.json"
    evidence = (
        json.loads(evidence_file.read_text())
        if evidence_file.exists()
        else {"conversations": {}, "requests": {}}
    )
    namespace = UUID(info["namespace"])
    selection = current_publication(state)
    selected_id = selection["publication_id"]
    selected_label = next(
        label
        for label, item in reversed(info["publications"].items())
        if item["publication_id"] == selected_id
    )
    if action in ("a", "b", "real"):
        require(selected_label == action, "Activate the expected publication first")
    if action == "b":
        require("a" in evidence["conversations"], "Check A before B")

    # Verify stored package bytes before and after querying every installed version.
    def verify_files():
        for baseline in info["publications"].values():
            package = catalog / baseline["publication_id"]
            for name, digest in baseline["files"].items():
                require(
                    file_sha256(package / name) == digest, "Publication bytes changed"
                )

    verify_files()
    async with httpx2.AsyncClient(trust_env=False, timeout=15) as http:
        response = await http.get(origin + "/health/ready")
        response.raise_for_status()
        require(
            response.json()["publication_id"] == selected_id,
            "Readiness selected another publication",
        )
        # A missing credential must fail even though this test endpoint is loopback-only.
        require(
            (await http.post(origin + "/mcp", json={})).status_code == 401,
            "MCP accepted an unauthenticated call",
        )
        async with httpx2.AsyncClient(  # noqa: SIM117 - SDK transport lifetimes are explicit.
            headers={"Authorization": "Bearer " + os.environ["VOC_MCP_TOKEN"]},
            trust_env=False,
            timeout=15,
        ) as secured:
            async with streamable_http_client(
                origin + "/mcp", http_client=secured
            ) as streams:
                async with ClientSession(*streams, read_timeout_seconds=15) as session:
                    await session.initialize()
                    require(
                        {tool.name for tool in (await session.list_tools()).tools}
                        == {"voc_get_data_state", "voc_ask"},
                        "Unexpected MCP surface",
                    )
                    for baseline in info["publications"].values():
                        identity = baseline["publication_id"]
                        for kind in KINDS:
                            expected = baseline["results"][kind]
                            validate_result(expected, identity, kind)
                            require(
                                query(catalog, identity, kind) == expected,
                                "Library query changed",
                            )
                            cli = await asyncio.to_thread(
                                subprocess.run,
                                [
                                    sys.executable,
                                    "-m",
                                    "voc.cli",
                                    "publication",
                                    "query",
                                    "--catalog",
                                    str(catalog),
                                    "--publication-id",
                                    identity,
                                    "--kind",
                                    kind,
                                ],
                                check=True,
                                capture_output=True,
                                text=True,
                                timeout=20,
                            )
                            require(
                                json.loads(cli.stdout) == expected, "CLI query differs"
                            )
                            response = await http.get(
                                f"{origin}/api/v1/publications/{identity}/{kind}"
                            )
                            response.raise_for_status()
                            require(response.json() == expected, "HTTP query differs")
                            result = await session.call_tool(
                                "voc_get_data_state",
                                {"publication_id": identity, "kind": kind},
                            )
                            require(
                                not result.is_error
                                and result.structured_content["result"] == expected,
                                "MCP query differs",
                            )
                    active = await session.call_tool(
                        "voc_get_data_state", {"kind": "overview"}
                    )
                    require(
                        active.structured_content["result"]["publication_id"]
                        == selected_id,
                        "MCP current differs",
                    )

                    async def ask(name, question, conversation_id=None):
                        # Stable per-run IDs make retry after disconnection or recreation harmless.
                        arguments = {
                            "request_id": str(uuid5(namespace, name)),
                            "question": question,
                        }
                        if conversation_id is not None:
                            arguments["conversation_id"] = conversation_id
                        for _ in range(100):
                            result = await session.call_tool("voc_ask", arguments)
                            body = result.structured_content
                            require(not result.is_error, "MCP question failed")
                            if body["status"] != "running":
                                break
                            await asyncio.sleep(0.05)
                        require(
                            body["status"] == "completed",
                            "Fixture question did not complete",
                        )
                        conversation = body["conversation"]
                        for turn in conversation["turns"]:
                            require(
                                turn["status"] == "completed"
                                and turn["planner_mode"] == "fixture",
                                "Unexpected turn state",
                            )
                            answer = turn["answer"]
                            require(
                                answer["usage"]["requests"] == 0,
                                "Probe unexpectedly invoked a model",
                            )
                            for result in answer["results"]:
                                validate_result(
                                    result,
                                    conversation["publication_id"],
                                    result["kind"],
                                )
                        evidence["requests"][name] = arguments
                        return conversation

                    # Readbacks and exact retries must not change any saved turn.
                    for label, saved in evidence["conversations"].items():
                        response = await http.get(
                            f"{origin}/api/v1/conversations/{saved['conversation_id']}"
                        )
                        response.raise_for_status()
                        require(
                            response.json() == saved,
                            f"Saved conversation {label} changed",
                        )
                    for arguments in evidence["requests"].values():
                        result = await session.call_tool("voc_ask", arguments)
                        require(
                            not result.is_error
                            and result.structured_content["status"] == "completed",
                            "Saved request did not survive",
                        )
                        saved = next(
                            item
                            for item in evidence["conversations"].values()
                            if item["conversation_id"]
                            == result.structured_content["conversation"][
                                "conversation_id"
                            ]
                        )
                        require(
                            result.structured_content["conversation"] == saved,
                            "Retry changed the conversation",
                        )

                    if action in ("a", "real"):
                        first = await ask(
                            action + "-play",
                            "¿Cuántos registros tenemos de Google Play?",
                        )
                        conversation = await ask(
                            action + "-apple",
                            "¿y de App Store?",
                            first["conversation_id"],
                        )
                        evidence["conversations"][action] = conversation
                    elif action == "b":
                        # A remains fixed while a new conversation observes B.
                        a = await ask(
                            "a-after-b",
                            "¿Cuántos registros tenemos de App Store?",
                            evidence["conversations"]["a"]["conversation_id"],
                        )
                        require(
                            a["publication_id"]
                            == info["publications"]["a"]["publication_id"],
                            "Old conversation switched publication",
                        )
                        require(
                            "4 registros de App Store"
                            in a["turns"][-1]["answer"]["text"],
                            "Old conversation lost A's counts",
                        )
                        evidence["conversations"]["a"] = a
                        conversation = await ask(
                            "b-apple", "¿Cuántos registros tenemos de App Store?"
                        )
                        require(
                            "5 registros de App Store"
                            in conversation["turns"][-1]["answer"]["text"],
                            "New conversation did not select B",
                        )
                        evidence["conversations"]["b"] = conversation
                    elif action == "independence":
                        evidence["conversations"]["independence"] = await ask(
                            "operator-stopped",
                            "¿Cuántos registros tenemos de Google Play?",
                        )
                    elif action != "check":
                        raise ValueError("Unknown probe action")

    verify_files()
    temporary = evidence_file.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, indent=2))
    temporary.replace(evidence_file)
    print(
        json.dumps(
            {
                "action": action,
                "active": selected_label,
                "publication_id": selected_id,
                "interfaces_agree": ["library", "CLI", "HTTP", "MCP"],
                "package_bytes_unchanged": True,
                "model_requests": 0,
                "conversations": {
                    label: item["conversation_id"]
                    for label, item in evidence["conversations"].items()
                },
                "missing_ml_modules": [
                    name
                    for name in ("torch", "zenml", "mlflow", "marimo", "jupyterlab")
                    if importlib.util.find_spec(name) is None
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1]))
