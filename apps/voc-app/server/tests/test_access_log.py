"""The access log keeps paths and drops query strings, where sign-in codes travel."""

import logging

from voc_app import access_log


def access_record(request_target, status=303):
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", request_target, "1.1", status),
        None,
    )


def test_query_strings_are_redacted_and_plain_paths_untouched():
    record = access_record("/auth/callback?code=secret-code&state=opaque")
    assert access_log.QueryStringFilter().filter(record)
    assert (
        record.getMessage()
        == '127.0.0.1:1 - "GET /auth/callback?[redacted] HTTP/1.1" 303'
    )
    plain = access_record("/api/v1/publications/current", 401)
    access_log.QueryStringFilter().filter(plain)
    assert '/api/v1/publications/current HTTP/1.1" 401' in plain.getMessage()


def test_other_records_pass_through_unchanged():
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 0, "started %s", ("?q=1",), None
    )
    assert access_log.QueryStringFilter().filter(record)
    assert record.getMessage() == "started ?q=1"


def test_importing_the_app_installs_the_filter_once():
    import voc_app.main  # noqa: F401

    logger = logging.getLogger("uvicorn.access")
    access_log.install()
    filters = [f for f in logger.filters if isinstance(f, access_log.QueryStringFilter)]
    assert len(filters) == 1
