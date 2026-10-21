import XCTest
import WebKit

@MainActor
final class SignInControllerTests: XCTestCase {
    let origin = URL(string: "http://localhost:8091")!

    override func tearDown() {
        AuthStubProtocol.handler = nil
        AuthStubProtocol.requests = []
    }

    private func configuration() -> URLSessionConfiguration {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AuthStubProtocol.self]
        return configuration
    }

    private func serve(loginOffered: Bool = true) {
        let base = NativeAuthTests()
        AuthStubProtocol.handler = { request in
            let url = request.url!.absoluteString
            if url == "http://localhost:8091/auth/config" {
                return loginOffered ? (200, try! JSONEncoder().encode(base.config)) : (404, Data())
            }
            if url == "\(NativeAuthTests.domain)/oauth2/token" {
                let body: [String: Any] = [
                    "access_token": base.mintAccessToken(), "expires_in": 3600, "token_type": "Bearer",
                    "id_token": base.mintIDToken(nonce: AuthStubProtocol.lastNonce), "refresh_token": "refresh-token-value",
                ]
                return (200, try! JSONSerialization.data(withJSONObject: body))
            }
            if url == "http://localhost:8091/auth/desktop-session" {
                guard request.value(forHTTPHeaderField: "Authorization")?.hasPrefix("Bearer ") == true else { return (401, Data()) }
                let body: [String: Any] = ["cookie": ["name": "voc_session", "value": "cookie-value-123", "max_age": 1800], "email": "a@example.com"]
                return (200, try! JSONSerialization.data(withJSONObject: body))
            }
            if url == "http://localhost:8091/auth/logout" { return (200, Data(#"{"logout_url":"x"}"#.utf8)) }
            return (404, Data())
        }
    }

    func testSignInInstallsTheWebCookieAndSignOutRemovesEverything() async throws {
        serve()
        let store = MemoryCredentialStore()
        let controller = SignInController(store: store, session: NonceCapturingSignIn(mode: .follow),
                                          configuration: configuration(), webData: .nonPersistent())
        var signedIn = 0, signedOut = 0
        controller.onSignedIn = { signedIn += 1 }
        controller.onSignedOut = { signedOut += 1 }
        await controller.refresh(origin: origin)
        XCTAssertEqual(controller.mode, .cognito)
        XCTAssertNil(controller.email)

        await controller.signIn(origin: origin)
        XCTAssertEqual(controller.email, "a@example.com")
        XCTAssertEqual(signedIn, 1)
        let cookies = await controller.webData.httpCookieStore.allCookies()
        let session = try XCTUnwrap(cookies.first { $0.name == "voc_session" })
        XCTAssertEqual(session.value, "cookie-value-123")
        XCTAssertEqual(session.domain, "localhost")
        XCTAssertEqual(session.path, "/")
        XCTAssertFalse(session.isSecure)
        let exchange = try XCTUnwrap(AuthStubProtocol.requests.first { $0.url!.path == "/auth/desktop-session" })
        XCTAssertEqual(exchange.httpMethod, "POST")
        let token = try await controller.bearer("http://localhost:8091")
        XCTAssertEqual("Bearer \(token!)", exchange.value(forHTTPHeaderField: "Authorization"))

        // A lost cookie is reissued from the Keychain without a second authentication window.
        XCTAssertEqual(controller.logoutHost, "test.auth.us-east-2.amazoncognito.com")
        await controller.webData.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast)
        let authorizations = { AuthStubProtocol.requests.filter { String(decoding: $0.httpBody ?? Data(), as: UTF8.self).contains("grant_type=authorization_code") }.count }
        let exchanges = { AuthStubProtocol.requests.filter { $0.url!.path == "/auth/desktop-session" }.count }
        let (beforeAuthorizations, beforeExchanges) = (authorizations(), exchanges())
        await controller.signIn(origin: origin)
        XCTAssertEqual(authorizations(), beforeAuthorizations)
        XCTAssertEqual(exchanges(), beforeExchanges + 1)
        XCTAssertEqual(signedIn, 2)
        let reissued = await controller.webData.httpCookieStore.allCookies()
        XCTAssertEqual(reissued.first { $0.name == "voc_session" }?.value, "cookie-value-123")

        await controller.signOut(origin: origin)
        XCTAssertNil(controller.email)
        XCTAssertEqual(signedOut, 1)
        XCTAssertNil(try store.load(origin: "http://localhost:8091"))
        let remaining = await controller.webData.httpCookieStore.allCookies()
        XCTAssertTrue(remaining.isEmpty)
        let logout = try XCTUnwrap(AuthStubProtocol.requests.first { $0.url!.path == "/auth/logout" })
        XCTAssertEqual(logout.value(forHTTPHeaderField: "Cookie"), "voc_session=cookie-value-123")
        XCTAssertEqual(logout.value(forHTTPHeaderField: "Origin"), "http://localhost:8091")
        XCTAssertNil(logout.value(forHTTPHeaderField: "Authorization"))
        do { _ = try await controller.bearer("http://localhost:8091"); XCTFail("expected sign_in_required") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "sign_in_required") }
    }

    func testARestartRestoresTheWebCookieFromTheKeychainWithoutAWindow() async throws {
        serve()
        let store = MemoryCredentialStore()
        let first = SignInController(store: store, session: NonceCapturingSignIn(mode: .follow),
                                     configuration: configuration(), webData: .nonPersistent())
        await first.signIn(origin: origin)
        XCTAssertNotNil(try store.load(origin: "http://localhost:8091"))
        // A new process: same Keychain, empty web store, a sign-in session that would fail if used.
        let second = SignInController(store: store, session: FakeSignIn(mode: .cancel),
                                      configuration: configuration(), webData: .nonPersistent())
        var restored = 0
        second.onSignedIn = { restored += 1 }
        await second.refresh(origin: origin)
        XCTAssertEqual(second.email, "a@example.com")
        await second.restore(origin: origin)
        XCTAssertEqual(restored, 1)
        let cookies = await second.webData.httpCookieStore.allCookies()
        XCTAssertEqual(cookies.first { $0.name == "voc_session" }?.value, "cookie-value-123")
        await second.restore(origin: origin)
        XCTAssertEqual(restored, 1)
    }

    func testLocalServersNeedNoTokenAndAreProbedOnce() async throws {
        serve(loginOffered: false)
        let controller = SignInController(store: MemoryCredentialStore(), session: FakeSignIn(mode: .cancel),
                                          configuration: configuration(), webData: .nonPersistent())
        await controller.refresh(origin: origin)
        XCTAssertEqual(controller.mode, .local)
        let first = try await controller.bearer("http://localhost:8091")
        let second = try await controller.bearer("http://localhost:8091")
        XCTAssertNil(first)
        XCTAssertNil(second)
        XCTAssertEqual(AuthStubProtocol.requests.filter { $0.url!.path == "/auth/config" }.count, 1)
    }
}
