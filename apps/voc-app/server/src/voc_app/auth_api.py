"""Browser sign-in through Cognito; tokens stay on the server behind an opaque cookie."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .auth import COOKIE, AuthError, Identity, current_user
from .data_api import failure, reject_query_parameters
from .sessions import SESSION_TTL

router = APIRouter(prefix="/auth", tags=["Sign-in"])
NO_STORE = {"Cache-Control": "no-store"}


def cognito_auth(request):
    # Local mode has no sign-in pages at all.
    if request.app.state.settings.auth_mode != "cognito":
        raise failure(404, "route_not_found", "This route does not exist.")
    return request.app.state.auth


def require_same_origin(request):
    # A browser always sends Origin on cross-site writes; the app's own page sends ours.
    if request.headers.get("origin") != request.app.state.settings.public_origin:
        raise failure(
            403, "origin_not_allowed", "Envía esta petición desde la aplicación VOC."
        )


def secure_cookies(request):
    return request.app.state.settings.public_origin.startswith("https://")


def failed_login(code):
    # Top-level navigations cannot read JSON errors; the sign-in page shows the code.
    return RedirectResponse(f"/?login_error={code}", status_code=303, headers=NO_STORE)


@router.get("/login")
def login(request: Request):
    reject_query_parameters(request)
    auth = cognito_auth(request)
    state, nonce, verifier = auth.sessions.begin_login("/")
    return RedirectResponse(
        auth.cognito.authorize_url(state, nonce, verifier),
        status_code=302,
        headers=NO_STORE,
    )


@router.get("/callback")
def callback(request: Request):
    auth = cognito_auth(request)
    params = request.query_params
    if set(params) - {"code", "state", "error", "error_description"} or any(
        len(params.getlist(name)) > 1 for name in params
    ):
        raise failure(422, "invalid_request", "Invalid request parameters.")
    pending = auth.sessions.take_login(params["state"]) if params.get("state") else None
    if pending is None:
        return failed_login("login_state_invalid")
    if "error" in params or not params.get("code"):
        return failed_login("login_cancelled")
    try:
        tokens = auth.cognito.exchange(params["code"], pending["code_verifier"])
        claims = auth.verifier.id_claims(
            str(tokens.get("id_token", "")), pending["nonce"]
        )
        identity = auth.verifier.access_identity(tokens["access_token"])
    except AuthError as error:
        return failed_login(error.code)
    settings = request.app.state.settings
    if (
        identity.subject != claims["sub"]
        or identity.client_id != settings.cognito_web_client_id
        or not isinstance(tokens.get("refresh_token"), str)
    ):
        return failed_login("invalid_token")
    value = auth.sessions.create(
        identity.subject, str(claims["email"]), identity.client_id, tokens
    )
    response = RedirectResponse(pending["return_to"], status_code=303, headers=NO_STORE)
    response.set_cookie(
        COOKIE,
        value,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure_cookies(request),
    )
    return response


@router.get("/me")
def me(request: Request, identity: Annotated[Identity, Depends(current_user)]):
    reject_query_parameters(request)
    return {"email": identity.email, "mode": request.app.state.settings.auth_mode}


@router.post("/logout")
def logout(request: Request):
    reject_query_parameters(request)
    auth = cognito_auth(request)
    require_same_origin(request)
    cookie = request.cookies.get(COOKIE)
    if cookie:
        auth.sessions.delete(cookie)
    response = JSONResponse({"logout_url": auth.cognito.logout_url()}, headers=NO_STORE)
    response.delete_cookie(
        COOKIE, path="/", httponly=True, samesite="lax", secure=secure_cookies(request)
    )
    return response
