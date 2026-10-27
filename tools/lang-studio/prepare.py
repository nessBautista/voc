"""Validate the local publication and write pasteable inputs outside the demo stores."""

import asyncio
import json

from graph import REPO, settings
from voc_app.client import PublicationService

QUESTIONS = {
    "daily": "¿Cómo se sienten, en general, quienes dejaron reseñas?",
    "research": "Encuentra el mes con mayor proporción de comentarios negativos, compara sus temas y muestra reseñas del tema destacado",
}


async def main():
    selected = settings()
    publication_id = await PublicationService(selected).current()
    destination = REPO / "data/lang-studio"
    destination.mkdir(parents=True, exist_ok=True)
    for mode, question in QUESTIONS.items():
        sample = {
            "question": question,
            "mode": mode,
            "history": [],
            "memory": [],
            "publication_id": publication_id,
        }
        path = destination / f"input-{mode}.json"
        path.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n")
        print(f"{mode.title()} input: {path}")
    print(
        f"Graph: voc | planner: {selected.planner_mode} | publication: {publication_id}"
    )
    print("Local Studio runs only; AWS and desktop conversations are not attached.")


if __name__ == "__main__":
    asyncio.run(main())
