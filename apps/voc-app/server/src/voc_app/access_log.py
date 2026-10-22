"""Keep OAuth codes and other query strings out of the shipped access log."""

import logging

REDACTED = "?[redacted]"


class QueryStringFilter(logging.Filter):
    """Uvicorn logs the request line verbatim; the path is enough to find a failed request."""

    def filter(self, record):
        args = record.args
        if (
            isinstance(args, tuple)
            and len(args) == 5
            and isinstance(args[2], str)
            and "?" in args[2]
        ):
            path = args[2].split("?", 1)[0]
            record.args = (*args[:2], path + REDACTED, *args[3:])
        return True


def install():
    """Attach the filter once to uvicorn's access logger, whichever process imports the app."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(existing, QueryStringFilter) for existing in logger.filters):
        logger.addFilter(QueryStringFilter())
    return logger
