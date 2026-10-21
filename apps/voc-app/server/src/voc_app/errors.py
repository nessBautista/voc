"""Public error shapes shared by every router and the sign-in dependency."""

from fastapi import HTTPException


def failure(status, code, message):
    # Public errors describe recovery without exposing internal paths or traces.
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": message},
    )


def reject_query_parameters(request):
    # Do not silently ignore filters that this first API cannot apply.
    if request.query_params:
        raise failure(
            422,
            "invalid_request",
            "This endpoint does not accept query parameters.",
        )
