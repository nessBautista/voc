"""Expose VOC's actual graph to Studio without starting the HTTP app or its stores."""

import os
from pathlib import Path

from voc_app.agent.graph import build_graph
from voc_app.agent.planner import Planner
from voc_app.client import PublicationService
from voc_app.settings import Settings

REPO = Path(__file__).resolve().parents[2]


def settings():
    """Use explicit Studio settings; root .env must not select cloud auth or model mode."""
    selected = Settings(
        publications_dir=Path(
            os.environ.get("VOC_STUDIO_PUBLICATIONS_DIR", REPO / "data/voc-app/catalog")
        ).resolve(),
        # PublicationService reads only active.json here. No Conversations/Reports are started.
        state_dir=Path(
            os.environ.get(
                "VOC_STUDIO_PUBLICATION_STATE_DIR", REPO / "data/voc-app/state"
            )
        ).resolve(),
        planner_mode=os.environ.get("VOC_STUDIO_PLANNER_MODE", "fixture"),
        llm_base_url=os.environ.get("VOC_STUDIO_LLM_BASE_URL", Settings.llm_base_url),
        llm_model=os.environ.get("VOC_STUDIO_LLM_MODEL", Settings.llm_model),
        llm_api_key=os.environ.get("VOC_STUDIO_LLM_API_KEY", ""),
    )
    if selected.planner_mode == "model" and not selected.llm_api_key:
        raise ValueError(
            "Model mode needs VOC_STUDIO_LLM_API_KEY in config/lang-studio.local.env"
        )
    return selected


def create_graph(selected=None):
    selected = selected or settings()
    return build_graph(Planner(selected), PublicationService(selected))


graph = create_graph()
