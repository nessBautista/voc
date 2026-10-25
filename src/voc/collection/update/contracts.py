"""Collection result and its existing success requirement."""

from dataclasses import dataclass

from ..shared.serialization import encoded


@dataclass
class UpdateResult:
    revision_id: str | None
    report: dict

    def require_success(self):
        if self.revision_id is None or self.report["status"] != "success":
            raise RuntimeError(
                "Collection not committed: " + encoded(self.report["sources"])
            )
        return self
