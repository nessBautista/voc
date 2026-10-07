"""Receive a key on stdin inside the running container; never print its value."""

import os
import sys
import tempfile
from pathlib import Path

from voc_ml.llm.openrouter import SESSION_KEY_PATH


def main():
    key = sys.stdin.read().strip()
    if not key or key.startswith("op://") or any(c.isspace() for c in key):
        raise SystemExit("Expected one resolved OpenRouter key on stdin; no file changed.")
    path = Path(os.environ.get("OPENROUTER_API_KEY_FILE") or SESSION_KEY_PATH)
    # NamedTemporaryFile creates mode 0600. Replace atomically, including on rotation.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(key)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    print("OpenRouter session key saved; existing notebooks can now create a client.")


if __name__ == "__main__":
    main()
