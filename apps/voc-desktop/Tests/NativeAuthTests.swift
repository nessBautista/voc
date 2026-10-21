import XCTest

/// Answers the server's config route and Cognito's token endpoint inside the test process.
final class AuthStubProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) -> (Int, Data))?
    nonisolated(unsafe) static var requests: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var request = self.request
        if let stream = request.httpBodyStream {
            stream.open()
            var body = Data()
            let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 4096)
            while stream.hasBytesAvailable {
                let read = stream.read(buffer, maxLength: 4096)
                if read <= 0 { break }
                body.append(buffer, count: read)
            }
            buffer.deallocate()
            request.httpBody = body
        }
        Self.requests.append(request)
        let (status, data) = Self.handler?(request) ?? (404, Data())
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

struct FakeSignIn: SignInSession {
    enum Mode { case follow, cancel, foreignState, error }
    let mode: Mode
    func authenticate(_ url: URL, callbackScheme: String) async throws -> URL {
        let state = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.first { $0.name == "state" }!.value!
        switch mode {
        case .follow: return URL(string: "voc-data-state://auth/callback?code=test-code&state=\(state)")!
        case .foreignState: return URL(string: "voc-data-state://auth/callback?code=test-code&state=someone-elses")!
        case .error: return URL(string: "voc-data-state://auth/callback?error=access_denied&state=\(state)")!
        case .cancel: throw URLError(.cancelled)
        }
    }
}

final class NativeAuthTests: XCTestCase {
    static let issuer = "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_TEST"
    static let domain = "https://test.auth.us-east-2.amazoncognito.com"
    let origin = URL(string: "http://localhost:8091")!
    let config = AuthConfig(issuer: issuer, domain: domain, nativeClientID: "native-client-id",
                            scopes: ["openid", "email", "voc-api/access"], callback: AuthConfig.callbackURL,
                            publicOrigin: "http://localhost:8091")

    override func tearDown() {
        AuthStubProtocol.handler = nil
        AuthStubProtocol.requests = []
    }

    func testPKCEUsesTheReferenceVector() {
        XCTAssertEqual(PKCE.challenge(for: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        let verifier = try! PKCE.random(bytes: 48)
        XCTAssertEqual(verifier.count, 64)
        XCTAssertTrue(verifier.allSatisfy { $0.isLetter || $0.isNumber || "-_".contains($0) })
        XCTAssertNotEqual(try! PKCE.random(), try! PKCE.random())
    }

    func testConfigIsValidatedAgainstTheConfiguredOrigin() throws {
        XCTAssertNoThrow(try config.validate(origin: origin))
        XCTAssertThrowsError(try config.validate(origin: URL(string: "http://127.0.0.1:8091")!)) {
            XCTAssertEqual(($0 as? HandoffError)?.code, "origin_mismatch")
        }
        let bad = [
            AuthConfig(issuer: "http://cognito-idp.us-east-2.amazonaws.com/x", domain: Self.domain, nativeClientID: "n", scopes: ["openid", "voc-api/access"], callback: AuthConfig.callbackURL, publicOrigin: "http://localhost:8091"),
            AuthConfig(issuer: Self.issuer, domain: Self.domain, nativeClientID: "n", scopes: ["openid", "voc-api/access"], callback: "voc-data-state://handoff/x", publicOrigin: "http://localhost:8091"),
            AuthConfig(issuer: Self.issuer, domain: Self.domain, nativeClientID: "", scopes: ["openid", "voc-api/access"], callback: AuthConfig.callbackURL, publicOrigin: "http://localhost:8091"),
            AuthConfig(issuer: Self.issuer, domain: Self.domain, nativeClientID: "n", scopes: ["openid"], callback: AuthConfig.callbackURL, publicOrigin: "http://localhost:8091"),
            AuthConfig(issuer: Self.issuer, domain: "https://evil.example/path", nativeClientID: "n", scopes: ["openid", "voc-api/access"], callback: AuthConfig.callbackURL, publicOrigin: "http://localhost:8091"),
        ]
        for item in bad {
            XCTAssertThrowsError(try item.validate(origin: origin)) { XCTAssertEqual(($0 as? HandoffError)?.code, "invalid_auth_config") }
        }
    }

    func testAttemptBuildsTheAuthorizeURLAndAcceptsOnlyItsOwnCallback() throws {
        let attempt = try AuthAttempt(origin: origin, config: config)
        let parts = URLComponents(url: attempt.authorizeURL, resolvingAgainstBaseURL: false)!
        XCTAssertEqual("\(parts.scheme!)://\(parts.host!)\(parts.path)", "\(Self.domain)/oauth2/authorize")
        let query = Dictionary(uniqueKeysWithValues: parts.queryItems!.map { ($0.name, $0.value!) })
        XCTAssertEqual(query["response_type"], "code")
        XCTAssertEqual(query["client_id"], "native-client-id")
        XCTAssertEqual(query["redirect_uri"], AuthConfig.callbackURL)
        XCTAssertEqual(query["scope"], "openid email voc-api/access")
        XCTAssertEqual(query["code_challenge_method"], "S256")
        XCTAssertEqual(query["code_challenge"], PKCE.challenge(for: attempt.verifier))
        XCTAssertEqual(query["state"], attempt.state)
        XCTAssertEqual(query["nonce"], attempt.nonce)

        XCTAssertEqual(try attempt.code(from: URL(string: "voc-data-state://auth/callback?code=abc-123&state=\(attempt.state)")!), "abc-123")
        for bad in ["voc-data-state://auth/callback?code=abc&state=other",
                    "voc-data-state://auth/callback?code=abc",
                    "voc-data-state://handoff/callback?code=abc&state=\(attempt.state)",
                    "https://auth/callback?code=abc&state=\(attempt.state)",
                    "voc-data-state://auth/callback?code=abc&state=\(attempt.state)&state=\(attempt.state)",
                    "voc-data-state://auth/callback?code=a%20b&state=\(attempt.state)",
                    "voc-data-state://auth/callback?code=abc&state=\(attempt.state)#x"] {
            XCTAssertThrowsError(try attempt.code(from: URL(string: bad)!), bad) {
                XCTAssertEqual(($0 as? HandoffError)?.code, "auth_callback_rejected", bad)
            }
        }
        XCTAssertThrowsError(try attempt.code(from: URL(string: "voc-data-state://auth/callback?error=access_denied&state=\(attempt.state)")!)) {
            XCTAssertEqual(($0 as? HandoffError)?.code, "sign_in_cancelled")
        }
    }

    // MARK: - Token exchange through the stubbed endpoints

    func token(_ claims: [String: Any]) -> String {
        let header = Data(#"{"alg":"RS256","kid":"test"}"#.utf8).base64URLEncodedString()
        let payload = try! JSONSerialization.data(withJSONObject: claims).base64URLEncodedString()
        return "\(header).\(payload).c2lnbmF0dXJl"
    }

    func mintAccessToken(subject: String = "user-a", client: String = "native-client-id", scope: String = "openid email voc-api/access") -> String {
        token(["iss": Self.issuer, "sub": subject, "client_id": client, "token_use": "access", "scope": scope,
               "exp": Date().timeIntervalSince1970 + 3600])
    }

    func mintIDToken(nonce: String, subject: String = "user-a") -> String {
        token(["iss": Self.issuer, "sub": subject, "aud": "native-client-id", "token_use": "id", "nonce": nonce,
               "email": "a@example.com", "exp": Date().timeIntervalSince1970 + 3600])
    }

    private func configuration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AuthStubProtocol.self]
        return configuration
    }

    private func serveCognito(refresh: Bool = true, nonceOverride: String? = nil, tokenStatus: Int = 200, configStatus: Int = 200) {
        let config = self.config
        AuthStubProtocol.handler = { request in
            let url = request.url!.absoluteString
            if url == "http://localhost:8091/auth/config" {
                return (configStatus, try! JSONEncoder().encode(config))
            }
            if url == "\(Self.domain)/oauth2/token" {
                let form = Dictionary(uniqueKeysWithValues: String(decoding: request.httpBody ?? Data(), as: UTF8.self)
                    .split(separator: "&").map { pair -> (String, String) in
                        let kv = pair.split(separator: "=", maxSplits: 1).map { String($0).removingPercentEncoding! }
                        return (kv[0], kv.count > 1 ? kv[1] : "")
                    })
                var body: [String: Any] = ["access_token": self.mintAccessToken(), "expires_in": 3600, "token_type": "Bearer"]
                if form["grant_type"] == "authorization_code" {
                    body["id_token"] = self.mintIDToken(nonce: nonceOverride ?? form["__nonce__"] ?? AuthStubProtocol.lastNonce)
                    if refresh { body["refresh_token"] = "refresh-token-value" }
                }
                return (tokenStatus, try! JSONSerialization.data(withJSONObject: body))
            }
            return (404, Data())
        }
    }

    func testSignInStoresTokensBoundToTheOriginAndIssuer() async throws {
        serveCognito()
        let store = MemoryCredentialStore()
        let auth = NativeAuth(store: store, session: NonceCapturingSignIn(mode: .follow), configuration: configuration())
        let tokens = try await auth.signIn(origin: origin)
        XCTAssertEqual(tokens.origin, "http://localhost:8091")
        XCTAssertEqual(tokens.issuer, Self.issuer)
        XCTAssertEqual(tokens.subject, "user-a")
        XCTAssertEqual(tokens.email, "a@example.com")
        XCTAssertEqual(tokens.refreshToken, "refresh-token-value")
        XCTAssertTrue(tokens.isFresh())
        XCTAssertEqual(try store.load(origin: "http://localhost:8091"), tokens)
        let exchange = AuthStubProtocol.requests.first { $0.url!.path == "/oauth2/token" }!
        let form = String(decoding: exchange.httpBody!, as: UTF8.self)
        XCTAssertTrue(form.contains("grant_type=authorization_code") && form.contains("code=test-code") && form.contains("code_verifier="))
        XCTAssertFalse(form.contains("client_secret"))
        let fresh = try await auth.accessToken(origin: origin)
        XCTAssertEqual(fresh, tokens.accessToken)
    }

    func testForeignCallbacksCancellationsAndBadTokensLeaveNothingStored() async throws {
        let store = MemoryCredentialStore()
        for (mode, code) in [(FakeSignIn.Mode.foreignState, "auth_callback_rejected"), (.cancel, "sign_in_cancelled"), (.error, "sign_in_cancelled")] {
            serveCognito()
            let auth = NativeAuth(store: store, session: FakeSignIn(mode: mode), configuration: configuration())
            do { _ = try await auth.signIn(origin: origin); XCTFail("expected \(code)") }
            catch { XCTAssertEqual((error as? HandoffError)?.code, code) }
        }
        serveCognito(nonceOverride: "another-attempt")
        let mismatched = NativeAuth(store: store, session: NonceCapturingSignIn(mode: .follow), configuration: configuration())
        do { _ = try await mismatched.signIn(origin: origin); XCTFail("expected sign_in_failed") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "sign_in_failed") }
        serveCognito(tokenStatus: 400)
        let refused = NativeAuth(store: store, session: NonceCapturingSignIn(mode: .follow), configuration: configuration())
        do { _ = try await refused.signIn(origin: origin); XCTFail("expected sign_in_failed") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "sign_in_failed") }
        XCTAssertNil(try store.load(origin: "http://localhost:8091"))
    }

    func testExpiredTokensRefreshOnceThenRequireSignIn() async throws {
        serveCognito()
        let store = MemoryCredentialStore()
        let expired = TokenSet(origin: "http://localhost:8091", issuer: Self.issuer, subject: "user-a", email: nil,
                               accessToken: "old", refreshToken: "refresh-token-value", expiresAt: Date().timeIntervalSince1970 - 10)
        try store.save(expired)
        let auth = NativeAuth(store: store, session: FakeSignIn(mode: .cancel), configuration: configuration())
        let renewed = try await auth.accessToken(origin: origin)
        XCTAssertNotEqual(renewed, "old")
        XCTAssertTrue(try store.load(origin: "http://localhost:8091")!.isFresh())
        let refresh = AuthStubProtocol.requests.last { $0.url!.path == "/oauth2/token" }!
        XCTAssertTrue(String(decoding: refresh.httpBody!, as: UTF8.self).contains("grant_type=refresh_token"))

        try store.save(expired)
        serveCognito(tokenStatus: 400)
        do { _ = try await auth.accessToken(origin: origin); XCTFail("expected sign_in_required") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "sign_in_required") }
        XCTAssertNil(try store.load(origin: "http://localhost:8091"))

        var noRefresh = expired
        noRefresh.refreshToken = nil
        try store.save(noRefresh)
        do { _ = try await auth.accessToken(origin: origin); XCTFail("expected sign_in_required") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "sign_in_required") }
    }

    func testLocalModeServersAndChangedIssuersAreReported() async throws {
        serveCognito(configStatus: 404)
        let auth = NativeAuth(store: MemoryCredentialStore(), session: FakeSignIn(mode: .follow), configuration: configuration())
        do { _ = try await auth.config(for: origin); XCTFail("expected server_without_login") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "server_without_login") }
        do { _ = try await auth.accessToken(origin: origin); XCTFail("expected sign_in_required") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "sign_in_required") }
        try await auth.signOut(origin: origin)
    }
}

/// Follows the authorize URL like a browser would, remembering the nonce the stub must echo.
struct NonceCapturingSignIn: SignInSession {
    let mode: FakeSignIn.Mode
    func authenticate(_ url: URL, callbackScheme: String) async throws -> URL {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!
        AuthStubProtocol.lastNonce = items.first { $0.name == "nonce" }!.value!
        return try await FakeSignIn(mode: mode).authenticate(url, callbackScheme: callbackScheme)
    }
}

extension AuthStubProtocol {
    nonisolated(unsafe) static var lastNonce = ""
}
