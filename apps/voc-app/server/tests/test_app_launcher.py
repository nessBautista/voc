"""Check credential routing with fake host tools and synthetic values only."""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    "target,mode,planner,mcp",
    [
        ("app", "env-file", "fixture", "false"),
        ("app", "env-file", "fixture", "true"),
        ("app", "1password", "fixture", "true"),
        ("app", "env-file", "model", "true"),
        ("app", "1password", "model", "true"),
        ("operator", "env-file", "fixture", "false"),
        ("operator", "1password", "fixture", "false"),
    ],
)
def test_launcher_keeps_operator_and_app_destinations_separate(
    tmp_path, target, mode, planner, mcp
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("""#!/usr/bin/env python3
import json, os, sys
print(json.dumps({"args":sys.argv[1:], "project":os.getenv("COMPOSE_PROJECT_NAME"), "provider_present":bool(os.getenv("OPENROUTER_API_KEY")), "aws_present":bool(os.getenv("AWS_SECRET_ACCESS_KEY")), "mcp":os.getenv("VOC_MCP_TOKEN")}))
""")
    docker.chmod(0o755)
    op = bin_dir / "op"
    op.write_text("""#!/usr/bin/env python3
import os, sys
args=sys.argv[sys.argv.index("--")+1:]
os.environ["OPENROUTER_API_KEY"]="synthetic-provider"
os.environ["VOC_MCP_TOKEN"]="synthetic-mcp"
os.environ["AWS_SECRET_ACCESS_KEY"]="synthetic-aws"
os.execvp(args[0],args)
""")
    op.chmod(0o755)
    credential = tmp_path / "credential.env"
    if planner != "fixture" or target == "operator" or mcp == "true":
        credential.write_text("OPENROUTER_API_KEY='synthetic-provider'\n")
    (tmp_path / ".env").write_text("")
    result = subprocess.run(
        ["sh", str(REPO / "scripts/start.sh"), target],
        cwd=tmp_path,
        env={
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "VOC_AWS_ENV_FILE": str(credential),
            "VOC_AWS_AUTH_MODE": mode,
            "VOC_PLANNER_MODE": planner,
            "VOC_MCP_ENABLED": mcp,
            "VOC_MCP_TOKEN": "stale-mcp",
            "COMPOSE_PROJECT_NAME": "operator-custom",
            "OPENROUTER_API_KEY": "stale-synthetic",
            "AWS_SECRET_ACCESS_KEY": "stale-synthetic",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    called = json.loads(result.stdout)
    assert f"docker/{target}/compose.yaml" in called["args"]
    if target == "app" and planner == "fixture" and mcp == "false":
        assert called["args"][called["args"].index("-p") + 1] == "voc-state-app"
        assert not called["provider_present"]
        assert not called["mcp"]
        assert str(credential) not in called["args"]
    else:
        assert called["project"] == (
            "voc-state-app" if target == "app" else "operator-custom"
        )
        if mode == "env-file":
            assert str(credential) in called["args"]
            assert not called["provider_present"]
            assert not called["aws_present"]
            assert called["mcp"] is None
        else:
            assert called["provider_present"] == (target != "app" or planner == "model")
            assert called["mcp"] == "synthetic-mcp"
            assert str(credential) not in called["args"]
