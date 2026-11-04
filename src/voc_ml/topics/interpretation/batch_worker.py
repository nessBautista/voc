"""Private workflow entry point used by the bounded batch adapter."""

import sys

from .ledger import InterpretationStore
from .tracking import execute_workflow


def main():
    try:
        result = execute_workflow(sys.argv[1], store=InterpretationStore(sys.argv[2]))
        if result["state"] != "accepted" or not result.get("tracking_verified"):
            return 1
        return 0
    except Exception:  # noqa: BLE001 - error details stay out of process output
        return 1


if __name__ == "__main__":
    sys.exit(main())
