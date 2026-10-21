"""A stand-in for the pool's key set and token endpoints; tests never reach AWS."""

import time
from urllib.parse import parse_qs

import httpx
import jwt

ISSUER = "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_TEST"
DOMAIN = "https://test.auth.us-east-2.amazoncognito.com"
WEB, NATIVE = "web-client-id", "native-client-id"
ORIGIN = "http://localhost:8091"
SCOPE = "openid email voc-api/access"


def mint(key, **claims):
    issued = int(time.time())
    base = {"iss": ISSUER, "sub": "user-a", "iat": issued, "exp": issued + 3600}
    return jwt.encode(
        {**base, **claims}, key, algorithm="RS256", headers={"kid": "test-kid"}
    )


def access_token(key, **claims):
    return mint(
        key, **{"client_id": NATIVE, "token_use": "access", "scope": SCOPE, **claims}
    )


class FakeCognito:
    def __init__(self, key):
        self.key = key
        self.nonce = None
        self.subject = "user-a"
        self.email = "a@example.com"
        self.expires_in = 3600
        self.refuse = False
        self.requests = []

    @property
    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request):
        if str(request.url) == f"{ISSUER}/.well-known/jwks.json":
            public = jwt.algorithms.RSAAlgorithm.to_jwk(
                self.key.public_key(), as_dict=True
            )
            return httpx.Response(
                200,
                json={
                    "keys": [
                        {**public, "kid": "test-kid", "alg": "RS256", "use": "sig"}
                    ]
                },
            )
        if str(request.url) == f"{DOMAIN}/oauth2/userInfo":
            self.requests.append(request)
            token = request.headers.get("authorization", "")[len("Bearer ") :]
            try:
                claims = jwt.decode(token, options={"verify_signature": False})
            except jwt.PyJWTError:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(
                200,
                json={
                    "sub": claims.get("sub"),
                    "email": f"{claims.get('sub')}@example.com",
                },
            )
        if str(request.url) == f"{DOMAIN}/oauth2/token":
            self.requests.append(request)
            if self.refuse:
                return httpx.Response(400, json={"error": "invalid_grant"})
            form = parse_qs(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "id_token": mint(
                        self.key,
                        aud=WEB,
                        token_use="id",
                        email=self.email,
                        nonce=self.nonce,
                        sub=self.subject,
                    ),
                    "access_token": mint(
                        self.key,
                        client_id=WEB,
                        token_use="access",
                        scope=SCOPE,
                        sub=self.subject,
                    ),
                    "refresh_token": "refresh-after-" + form["grant_type"][0],
                    "expires_in": self.expires_in,
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(404)
