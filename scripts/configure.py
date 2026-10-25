"""Create private first-run settings; executed in a disposable Python container."""

import argparse
import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def create_private(path, content):
    """Never replace settings from an earlier setup or follow an existing symlink."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return True


def configure(root, member):
    member = re.sub(r"[^a-z0-9_-]+", "-", member.lower()).strip("-_")[:50] or "teammate"
    env = (root / ".env.example").read_text()
    env = env.replace(
        "JUPYTER_TOKEN=\n", "JUPYTER_TOKEN=" + secrets.token_hex(24) + "\n"
    )
    credentials = (root / "config/aws.local.env.example").read_text()
    env = env.replace("VOC_MEMBER_ID=personal", f"VOC_MEMBER_ID={member}")
    result = {}
    for relative, content in ((".env", env), ("config/aws.local.env", credentials)):
        result[relative] = create_private(root / relative, content)
        print(("Created " if result[relative] else "Preserved existing ") + relative)
    print("Leave config/aws.local.env keys empty for local work.")
    print(
        "Before using AWS, choose your own bucket and storage settings in .env, "
        "then fill the private credential file. Review the generated member ID."
    )
    print("Then run just up. Open notebooks with just marimo or just jupyter.")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--member", required=True)
    args = parser.parse_args()
    configure(ROOT, args.member)
