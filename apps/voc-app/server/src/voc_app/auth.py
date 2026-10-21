"""Identify the caller: a local developer, a browser session, or a Cognito access token."""

import base64
import hashlib
import secrets
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import Request

from .errors import failure
from .sessions import Sessions, now, parse

COOKIE = "voc_session"
REQUIRED_SCOPE = "voc-api/access"
RESPONSE_LIMIT = 65_536


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    email: str
    client_id: str
    # How the caller was recognised: local, cookie or bearer.
    source: str = "local"

    @property
    def owner(self):
        # Emails change; the issuer and subject pair does not.
        return f"{self.issuer}#{self.subject}"


LOCAL_DEVELOPER = Identity("local", "developer", "developer@localhost", "local")


class AuthError(Exception):
    """A sign-in step failed; the message is safe to show to the person."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class SigningKeys:
    """Fetch the pool's public keys once and cache them by key ID."""

    def __init__(self, issuer, transport=None):
        self.url = f"{issuer}/.well-known/jwks.json"
        self.client = httpx.Client(transport=transport, timeout=10, trust_env=False)
        self.keys, self.fetched = {}, None

    def get(self, kid):
        # Refresh on an unknown key ID, but not more than once a minute.
        if kid not in self.keys and (
            self.fetched is None or monotonic() - self.fetched > 60
        ):
            self.refresh()
        return self.keys.get(kid)

    def refresh(self):
        response = self.client.get(self.url)
        response.raise_for_status()
        if len(response.content) > RESPONSE_LIMIT:
            raise ValueError("Key set exceeds the limit")
        self.keys = {
            key["kid"]: jwt.PyJWK(key).key
            for key in response.json()["keys"]
            if key.get("kty") == "RSA" and key.get("kid")
        }
        self.fetched = monotonic()

    def close(self):
        self.client.close()


class TokenVerifier:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.keys = SigningKeys(settings.cognito_issuer, transport)
        self.allowed_clients = {
            settings.cognito_web_client_id,
            settings.cognito_native_client_id,
        }

    def decode(self, token, *, require, audience=None):
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key = self.keys.get(kid) if kid else None
            if key is None:
                raise jwt.InvalidTokenError("Unknown signing key")
            return jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.settings.cognito_issuer,
                audience=audience,
                leeway=30,
                options={"require": ["exp", "iat", "iss", "sub", *require]},
            )
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, KeyError) as error:
            raise AuthError(
                "invalid_token", "La sesión no es válida. Inicia sesión de nuevo."
            ) from error

    def access_identity(self, token):
        """Accept only an access token for one of our two app clients with our scope."""
        claims = self.decode(token, require=["token_use", "client_id", "scope"])
        if (
            claims["token_use"] != "access"
            or claims["client_id"] not in self.allowed_clients
            or REQUIRED_SCOPE not in str(claims["scope"]).split()
        ):
            raise AuthError(
                "invalid_token", "La sesión no es válida. Inicia sesión de nuevo."
            )
        return Identity(claims["iss"], claims["sub"], "", claims["client_id"], "bearer")

    def id_claims(self, token, nonce):
        """Verify the ID token from a sign-in and bind it to the browser that started it."""
        claims = self.decode(
            token,
            require=["token_use", "nonce", "email"],
            audience=self.settings.cognito_web_client_id,
        )
        if claims["token_use"] != "id" or not secrets.compare_digest(
            str(claims["nonce"]), nonce
        ):
            raise AuthError(
                "invalid_token", "La sesión no es válida. Inicia sesión de nuevo."
            )
        return claims


class CognitoClient:
    """The two addresses people and the server use: the login domain's endpoints."""

    def __init__(self, settings, transport=None):
        self.settings = settings
        self.client = httpx.Client(
            transport=transport, timeout=10, trust_env=False, follow_redirects=False
        )

    @property
    def callback_url(self):
        return f"{self.settings.public_origin}/auth/callback"

    def authorize_url(self, state, nonce, verifier):
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        return f"{self.settings.cognito_domain}/oauth2/authorize?" + urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.cognito_web_client_id,
                "redirect_uri": self.callback_url,
                "scope": self.settings.cognito_scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    def exchange(self, code, verifier):
        return self.token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.callback_url,
                "code_verifier": verifier,
            }
        )

    def refresh(self, refresh_token):
        return self.token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    def token(self, form):
        # The client secret travels only here, as HTTP Basic credentials to Cognito.
        try:
            response = self.client.post(
                f"{self.settings.cognito_domain}/oauth2/token",
                data={"client_id": self.settings.cognito_web_client_id, **form},
                auth=(
                    self.settings.cognito_web_client_id,
                    self.settings.cognito_web_client_secret,
                ),
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200 or len(response.content) > RESPONSE_LIMIT:
                raise ValueError("Token endpoint refused the request")
            tokens = response.json()
            if (
                not isinstance(tokens, dict)
                or not isinstance(tokens.get("access_token"), str)
                or not isinstance(tokens.get("expires_in"), int)
            ):
                raise ValueError("Token response is incomplete")  # noqa: TRY004 - caught below as a sign-in failure.
            return tokens
        except (httpx.HTTPError, ValueError) as error:
            raise AuthError(
                "token_exchange_failed",
                "No pudimos completar el inicio de sesión. Vuelve a intentarlo.",
            ) from error

    def logout_url(self):
        return f"{self.settings.cognito_domain}/logout?" + urlencode(
            {
                "client_id": self.settings.cognito_web_client_id,
                "logout_uri": f"{self.settings.public_origin}/",
            }
        )

    def close(self):
        self.client.close()


class Auth:
    """Everything cognito mode needs, created once per application."""

    def __init__(self, settings, transport=None):
        self.settings = settings
        self.sessions = Sessions(settings.state_dir)
        self.verifier = TokenVerifier(settings, transport)
        self.cognito = CognitoClient(settings, transport)

    def session_identity(self, cookie):
        """Resolve a cookie to its identity, renewing the access token when it has expired."""
        session = self.sessions.read(cookie)
        if session is None:
            return None
        if parse(session["access_expires_at"]) <= now():
            try:
                tokens = self.cognito.refresh(session["refresh_token"])
                self.verifier.access_identity(tokens["access_token"])
            except AuthError:
                self.sessions.delete(cookie)
                return None
            self.sessions.renew(cookie, tokens)
        return Identity(
            self.settings.cognito_issuer,
            session["subject"],
            session["email"],
            session["client_id"],
            "cookie",
        )

    def close(self):
        self.verifier.keys.close()
        self.cognito.close()


def current_user(request: Request) -> Identity:
    """One dependency for every protected route; anything unrecognised is 401."""
    settings = request.app.state.settings
    if settings.auth_mode == "local":
        return LOCAL_DEVELOPER
    auth = request.app.state.auth
    cookie = request.cookies.get(COOKIE)
    if cookie:
        identity = auth.session_identity(cookie)
        if identity is not None:
            return identity
        raise failure(
            401, "session_expired", "Tu sesión terminó. Inicia sesión de nuevo."
        )
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        try:
            return auth.verifier.access_identity(header[len("Bearer ") :].strip())
        except AuthError as error:
            raise failure(401, error.code, error.message) from error
    raise failure(401, "sign_in_required", "Inicia sesión para usar VOC.")


def require_same_origin(request, identity, message):
    """Cookie-authenticated writes must come from our own page; bearer callers need no cookie."""
    origin = request.headers.get("origin")
    if identity.source == "cookie":
        if origin != request.app.state.settings.public_origin:
            raise failure(403, "origin_not_allowed", message)
    elif identity.source == "local" and origin:
        # Local mode keeps the loopback rule: another website must not spend model calls.
        parsed = urlsplit(origin)
        if parsed.scheme not in (
            "http",
            "https",
        ) or parsed.netloc != request.headers.get("host"):
            raise failure(403, "origin_not_allowed", message)
