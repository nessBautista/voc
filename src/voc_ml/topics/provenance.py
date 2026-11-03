"""Code provenance adapter. Git metadata can be absent in a mounted worktree."""

import subprocess
from pathlib import Path

from voc_ml.identity import code_identity


def code_provenance():
    root = Path(__file__).resolve().parents[3]

    def git_value(*args):
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None

    return {
        "code_identity": code_identity(),
        "git_commit": git_value("rev-parse", "HEAD"),
        "git_status": git_value("status", "--porcelain"),
    }
