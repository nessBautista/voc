"""Check persistent Hugging Face credentials without printing secret values."""

import argparse
import json
from pathlib import Path

from huggingface_hub import HfApi, constants, get_token

MODELS = (
    ("Banamex-voc-sentiment/voc-robertuito-sentiment-stores-es", "v3"),
    ("Banamex-voc-sentiment/voc-robertuito-sentiment-social-es", "v2"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Check both model revisions online; no weight download",
    )
    args = parser.parse_args()
    path = Path(constants.HF_TOKEN_PATH)
    result = {"credential_available": False, "remote_checked": False}
    try:
        result["credential_available"] = path.is_file() and bool(get_token())
        if not result["credential_available"]:
            result["error"] = "credential_missing_or_empty"
        elif path.stat().st_mode & 0o077:
            result["error"] = "credential_permissions_must_be_owner_only"
        elif args.remote:
            if constants.HF_HUB_OFFLINE:
                result["error"] = "explicit_online_mode_required"
            else:
                result["models"] = []
                api = HfApi(token=True)
                for repo, tag in MODELS:
                    info = api.model_info(repo, revision=tag, timeout=30)
                    result["models"].append(
                        {"repo": repo, "tag": tag, "revision": info.sha}
                    )
                result["remote_checked"] = True
    except Exception as error:  # noqa: BLE001 -- sanitize every credential/network failure
        # Exception messages may contain request details: only emit their type.
        result["error"] = type(error).__name__
    print(json.dumps(result, indent=2))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
