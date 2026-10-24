"""Write the web's conversation_v2.json from the server composer on the shared v2 fixture.

Run from the repository root with the server environment:
    uv run --project apps/voc-app/server --group dev python apps/voc-app/server/tests/export_conversation_fixture.py
Then format it with prettier. The web tests never hand-write an answer shape.
"""

import json
import sys
import tempfile
import time
from pathlib import Path
from runpy import run_path

from fastapi.testclient import TestClient
from voc_app.main import create_app
from voc_app.settings import Settings

from voc.publications.builder import build_bundle
from voc.publications.store import activate_bundle

REPO = Path(__file__).resolve().parents[4]
TARGET = REPO / "apps/voc-app/web/tests/fixtures/conversation_v2.json"
RESEARCH_TARGET = REPO / "apps/voc-app/web/tests/fixtures/conversation_research.json"
RESEARCH_QUESTION = (
    "Encuentra el mes con mayor proporción de comentarios negativos, "
    "compara sus temas y muestra reseñas del tema destacado"
)
QUESTIONS = [
    "¿Cómo se sienten, en general, quienes dejaron reseñas?",
    "¿Cuál es la queja principal?",
    "¿Cuándo aumentaron las quejas?",
    "¿Qué dicen exactamente los clientes?",
    "¿Qué problemas más específicos aparecen?",
    "¿Dónde convendría poner atención primero?",
]


def main():
    analysis = run_path(str(REPO / "tests/fixtures/data_state/analysis_example.py"))
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        bundle = build_bundle(analysis["analysis_description"](), root / "analysis")
        settings = Settings(publications_dir=root / "catalog", state_dir=root / "state")
        activate_bundle(bundle, settings.publications_dir, settings.state_dir)
        with TestClient(create_app(settings)) as client:
            conversation = None
            for number, question in enumerate(QUESTIONS, 1):
                response = client.post(
                    "/api/v1/ask",
                    json={
                        # Fixed request ids keep the conversation id stable between runs.
                        "request_id": f"00000000-0000-4000-8000-0000000000a{number}",
                        "question": question,
                        "conversation_id": conversation
                        and conversation["conversation_id"],
                    },
                )
                assert response.status_code == 202, response.text
                identity = response.json()["conversation_id"]
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    conversation = client.get(
                        f"/api/v1/conversations/{identity}"
                    ).json()
                    if all(t["status"] != "running" for t in conversation["turns"]):
                        break
                    time.sleep(0.02)
                last = conversation["turns"][-1]
                assert last["status"] == "completed", last
            TARGET.write_text(
                json.dumps(conversation, ensure_ascii=False, indent=2) + "\n"
            )
            print(TARGET, len(conversation["turns"]), "turns")
            # One research conversation: the architecture's example question.
            response = client.post(
                "/api/v1/ask",
                json={
                    "request_id": "00000000-0000-4000-8000-0000000000b1",
                    "question": RESEARCH_QUESTION,
                    "conversation_id": None,
                    "mode": "research",
                },
            )
            assert response.status_code == 202, response.text
            identity = response.json()["conversation_id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                conversation = client.get(f"/api/v1/conversations/{identity}").json()
                if all(t["status"] != "running" for t in conversation["turns"]):
                    break
                time.sleep(0.02)
            assert conversation["turns"][-1]["status"] == "completed", conversation
            RESEARCH_TARGET.write_text(
                json.dumps(conversation, ensure_ascii=False, indent=2) + "\n"
            )
            print(RESEARCH_TARGET, len(conversation["turns"]), "turns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
