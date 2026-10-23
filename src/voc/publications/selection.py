"""The one selection the dashboard and the agent send with an analysis query.

A selection names platforms, sentiment labels, topic assessments, a month range
and a search text, plus the parameters one kind needs: a topic, a sort order or
a page. It is validated and written in one canonical form, so two requests that
mean the same thing hash to the same result identifier, and nothing from it is
ever formatted into SQL; the queries bind every value.
"""

import re

PLATFORMS = ("google_play", "app_store", "x", "youtube")
LABELS = ("negative", "neutral", "positive")
ASSESSMENTS = ("single_feature", "mixed", "unclear", "uninterpreted", "unassigned")
SORTS = ("volume", "negative", "positive")
MAX_SEARCH_CHARS = 100
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 10
FIELDS = (
    "platforms",
    "labels",
    "assessments",
    "month_from",
    "month_to",
    "search",
    "topic_id",
    "sort",
    "page",
    "page_size",
)
# Which optional parameters each kind reads; anything else must stay unset.
KIND_PARAMETERS = {
    "summary": (),
    "topic_list": ("sort",),
    "timeline": ("topic_id",),
    "platforms": (),
    "topic_detail": ("topic_id",),
    "reviews": ("topic_id", "page", "page_size"),
}
ANALYSIS_KINDS = tuple(KIND_PARAMETERS)
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class SelectionError(ValueError):
    """The selection names something the package cannot filter by."""


def _subset(value, allowed, label):
    if value is None:
        return None
    if isinstance(value, str):
        value = [item for item in value.split(",")]
    if not isinstance(value, (list, tuple)):
        raise SelectionError(f"{label} must be a list")
    items = sorted({str(item).strip() for item in value})
    if not items or any(not item for item in items):
        raise SelectionError(f"{label} must name at least one value")
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise SelectionError(f"{label} has unknown values: " + ", ".join(unknown))
    # Every value selected means no filter; the canonical form says so with null.
    return None if len(items) == len(allowed) else items


def _month(value, label):
    if value is None:
        return None
    if not isinstance(value, str) or not _MONTH.match(value):
        raise SelectionError(f"{label} must be a month written as YYYY-MM")
    return value


def _integer(value, label, *, minimum, maximum=None):
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if (
        type(value) is not int
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        bound = (
            f"at least {minimum}"
            if maximum is None
            else f"between {minimum} and {maximum}"
        )
        raise SelectionError(f"{label} must be an integer {bound}")
    return value


def canonical_selection(raw, kind):
    """Validate a raw selection for one analysis kind and return its canonical form.

    ``raw`` is a mapping with any subset of FIELDS; lists may arrive as
    comma-separated text. The result always carries every field, null when
    unused, so it can travel inside a query envelope and be hashed with it.
    """
    if kind not in KIND_PARAMETERS:
        raise SelectionError(f"{kind} takes no selection")
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        raise SelectionError("A selection must be an object")
    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise SelectionError("Unknown selection fields: " + ", ".join(unknown))
    allowed = KIND_PARAMETERS[kind]
    for name in ("topic_id", "sort", "page", "page_size"):
        if raw.get(name) is not None and name not in allowed:
            raise SelectionError(f"{kind} does not take {name}")

    selection = {
        "platforms": _subset(raw.get("platforms"), PLATFORMS, "platforms"),
        "labels": _subset(raw.get("labels"), LABELS, "labels"),
        "assessments": _subset(raw.get("assessments"), ASSESSMENTS, "assessments"),
        "month_from": _month(raw.get("month_from"), "month_from"),
        "month_to": _month(raw.get("month_to"), "month_to"),
        "search": None,
        "topic_id": None,
        "sort": None,
        "page": None,
        "page_size": None,
    }
    if (
        selection["month_from"]
        and selection["month_to"]
        and selection["month_from"] > selection["month_to"]
    ):
        raise SelectionError("month_from must not be later than month_to")
    search = raw.get("search")
    if search is not None:
        if not isinstance(search, str):
            raise SelectionError("search must be text")
        search = search.strip()
        if len(search) > MAX_SEARCH_CHARS:
            raise SelectionError(
                f"search must be at most {MAX_SEARCH_CHARS} characters"
            )
        if any(ord(c) < 32 for c in search):
            raise SelectionError("search must not contain control characters")
        selection["search"] = search or None
    if "topic_id" in allowed:
        topic_id = raw.get("topic_id")
        if topic_id is None and kind == "topic_detail":
            raise SelectionError("topic_detail requires topic_id")
        if topic_id is not None:
            selection["topic_id"] = _integer(topic_id, "topic_id", minimum=-1)
    if "sort" in allowed:
        sort = raw.get("sort") or "volume"
        if sort not in SORTS:
            raise SelectionError("sort must be volume, negative or positive")
        selection["sort"] = sort
    if "page" in allowed:
        page = raw.get("page")
        size = raw.get("page_size")
        selection["page"] = _integer(1 if page is None else page, "page", minimum=1)
        selection["page_size"] = _integer(
            DEFAULT_PAGE_SIZE if size is None else size,
            "page_size",
            minimum=1,
            maximum=MAX_PAGE_SIZE,
        )
    return selection


def like_pattern(search):
    """A bound ILIKE pattern whose wildcards are literal characters of the search."""
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
