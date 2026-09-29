"""Fingerprint project code and dependency definitions, independent of Git commits."""
import hashlib
from pathlib import Path

def code_identity():
    root = Path(__file__).resolve().parents[2]
    h = hashlib.sha256()
    for path in sorted((root / "src").rglob("*.py")):
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    for name in ("pyproject.toml", "uv.lock"):
        path = root / name
        h.update(name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()
