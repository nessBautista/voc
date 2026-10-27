"""Studio reuses the real graph and reads publications without opening demo stores."""

import asyncio
import importlib.util
from pathlib import Path

import pytest
from voc_app.agent.graph import build_graph
from voc_app.agent.planner import Planner
from voc_app.client import PublicationService

REPO = Path(__file__).resolve().parents[4]


@pytest.fixture
def studio(monkeypatch):
    for name in ("VOC_STUDIO_PLANNER_MODE", "VOC_STUDIO_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location(
        "voc_studio_graph", REPO / "tools/lang-studio/graph.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_app_settings_cannot_turn_studio_into_cloud_or_model_mode(
    studio, monkeypatch
):
    monkeypatch.setenv("VOC_AUTH_MODE", "cognito")
    monkeypatch.setenv("VOC_PLANNER_MODE", "model")
    monkeypatch.setenv("VOC_LLM_API_KEY", "synthetic-inherited-key")
    monkeypatch.setenv("VOC_APP_STATE_DIR", "/must-not-be-used")
    selected = studio.settings()
    assert selected.auth_mode == "local"
    assert selected.planner_mode == "fixture"
    assert selected.llm_api_key == ""
    assert selected.state_dir == REPO / "data/voc-app/state"


def test_model_mode_requires_its_own_explicit_key(studio, monkeypatch):
    monkeypatch.setenv("VOC_STUDIO_PLANNER_MODE", "model")
    with pytest.raises(ValueError, match="VOC_STUDIO_LLM_API_KEY"):
        studio.settings()


def test_studio_exposes_the_application_graph_without_extra_nodes(studio, settings):
    actual = studio.create_graph(settings).get_graph().to_json()
    expected = (
        build_graph(Planner(settings), PublicationService(settings))
        .get_graph()
        .to_json()
    )
    assert actual == expected


@pytest.mark.parametrize(
    "mode,question",
    [
        ("daily", "¿Cómo se sienten, en general, quienes dejaron reseñas?"),
        (
            "research",
            "Encuentra el mes con mayor proporción de comentarios negativos, compara sus temas y muestra reseñas del tema destacado",
        ),
    ],
)
def test_studio_runs_real_queries_and_preserves_publication_state(
    studio, settings, v2_active, mode, question
):
    before = {
        p.relative_to(settings.state_dir): p.read_bytes()
        for p in settings.state_dir.rglob("*")
        if p.is_file()
    }

    async def run():
        publication_id = await PublicationService(settings).current()
        return await studio.create_graph(settings).ainvoke(
            {
                "question": question,
                "mode": mode,
                "history": [],
                "memory": [],
                "publication_id": publication_id,
            },
            config={"recursion_limit": 24},
        )

    result = asyncio.run(run())
    assert result["answer"]["mode"] == mode
    assert result["answer"]["usage"]["requests"] == 0
    assert result["answer"]["results"]
    if mode == "research":
        assert len(result["answer"]["steps"]) == 2
    after = {
        p.relative_to(settings.state_dir): p.read_bytes()
        for p in settings.state_dir.rglob("*")
        if p.is_file()
    }
    assert after == before
    assert not (settings.state_dir / "conversations.sqlite3").exists()
