"""Prove that socket loss and a killed HTTP process preserve accepted turns."""

import json
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx


def test_disconnect_and_process_restart(settings, active, tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    module = tmp_path / "probe_app.py"
    module.write_text("""
import asyncio
from voc_app.main import create_app
from voc_app.settings import Settings
from voc_app.agent.planner import Planner
settings = Settings.from_env()
class SlowPlanner(Planner):
    async def plan(self, question, history, mode="daily"):
        await asyncio.sleep(60 if question == "slow" else .2)
        return await super().plan("Fuentes", history)
app = create_app(settings, planner=SlowPlanner(settings))
""")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("VOC_", "AWS_", "OPENROUTER_", "LANGSMITH_", "LANGCHAIN_")
        )
    }
    env.update(
        VOC_PUBLICATIONS_DIR=str(settings.publications_dir),
        VOC_APP_STATE_DIR=str(settings.state_dir),
        PYTHONPATH=str(tmp_path),
    )

    def start():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "probe_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(150):
            try:
                if (
                    httpx.get(
                        origin + "/health/live", timeout=0.2, trust_env=False
                    ).status_code
                    == 200
                ):
                    return process
            except httpx.HTTPError:
                pass
            if process.poll() is not None:
                raise AssertionError("Probe server exited")
            time.sleep(0.05)
        process.kill()
        process.wait()
        raise AssertionError("Probe server did not start")

    process = start()
    try:
        request = {
            "request_id": str(uuid4()),
            "question": "Fuentes",
            "conversation_id": None,
        }
        body = json.dumps(request).encode()
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            connection.sendall(
                f"POST /api/v1/ask HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            assert b"202 Accepted" in connection.recv(65536)
            # Close before the planner's delayed answer becomes available.
        with httpx.Client(base_url=origin, trust_env=False, timeout=3) as client:
            response = client.post("/api/v1/ask", json=request)
            identity = response.json()["conversation_id"]
            for _ in range(100):
                conversation = client.get(f"/api/v1/conversations/{identity}").json()
                if conversation["turns"][0]["status"] == "completed":
                    break
                time.sleep(0.03)
            assert len(conversation["turns"]) == 1
            assert conversation["turns"][0]["status"] == "completed"
            unfinished = {
                "request_id": str(uuid4()),
                "question": "slow",
                "conversation_id": identity,
            }
            assert client.post("/api/v1/ask", json=unfinished).status_code == 202
            process.kill()
            process.wait(timeout=5)
            process = start()
            restored = client.get(f"/api/v1/conversations/{identity}").json()
            assert restored["turns"][0] == conversation["turns"][0]
            assert restored["turns"][1]["status"] == "interrupted"
            retry = client.post("/api/v1/ask", json=unfinished)
            assert retry.status_code == 200
            assert retry.json() == restored
    finally:
        process.terminate()
        process.wait(timeout=5)
