import AppKit
import AuthenticationServices
import Combine
import WebKit

/// Shows Cognito's login page in the system authentication window.
final class WebAuthSession: NSObject, SignInSession, ASWebAuthenticationPresentationContextProviding {
    func authenticate(_ url: URL, callbackScheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            Task { @MainActor in
                let session = ASWebAuthenticationSession(url: url, callbackURLScheme: callbackScheme) { callback, error in
                    if let callback { continuation.resume(returning: callback) }
                    else { continuation.resume(throwing: error ?? HandoffError("sign_in_cancelled")) }
                }
                session.prefersEphemeralWebBrowserSession = true
                session.presentationContextProvider = self
                if !session.start() { continuation.resume(throwing: HandoffError("sign_in_failed")) }
            }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        NSApp.keyWindow ?? NSApp.windows.first(where: \.isVisible) ?? ASPresentationAnchor()
    }
}

/// One person, one server: native credentials in the Keychain, a matching cookie in the web view.
@MainActor
final class SignInController: ObservableObject {
    enum Mode: Equatable { case unknown, local, cognito }

    @Published private(set) var mode: Mode = .unknown
    @Published private(set) var email: String?
    @Published private(set) var busy = false
    @Published private(set) var message = ""
    /// Host of the login domain, so the web view can recognise the page's sign-out.
    @Published private(set) var logoutHost: String?
    let webData: WKWebsiteDataStore
    private let auth: NativeAuth
    private let configuration: URLSessionConfiguration
    private var sessionCookie: String?
    var onSignedIn: (@MainActor () -> Void)?
    var onSignedOut: (@MainActor () -> Void)?

    init(store: any CredentialStore = KeychainCredentialStore(), session: any SignInSession = WebAuthSession(),
         configuration: URLSessionConfiguration = .ephemeral, webData: WKWebsiteDataStore = .nonPersistent()) {
        self.configuration = configuration
        self.webData = webData
        auth = NativeAuth(store: store, session: session, configuration: configuration)
    }

    /// What native requests attach: nil for a local-mode server, else a fresh access token.
    var bearer: BearerProvider {
        let auth = self.auth
        return { origin in try await auth.bearer(origin: origin) }
    }

    func refresh(origin: URL?) async {
        guard let origin else { return }
        do {
            mode = try await auth.hasLogin(origin: origin) ? .cognito : .local
            email = try await auth.current(origin: origin)?.email
            logoutHost = await auth.knownConfig(origin: origin).flatMap { URL(string: $0.domain)?.host?.lowercased() }
        } catch {
            mode = .unknown
            message = error.localizedDescription
        }
    }

    /// After a restart the Keychain still knows the person; give the web view its cookie back quietly.
    func restore(origin: URL?) async {
        guard let origin, !busy, mode == .cognito else { return }
        let cookies = await webData.httpCookieStore.allCookies()
        guard !cookies.contains(where: { $0.name == "voc_session" }) else { return }
        guard let accessToken = try? await auth.accessToken(origin: origin) else {
            email = nil
            return
        }
        do {
            try await installWebSession(origin: origin, accessToken: accessToken)
            email = try await auth.current(origin: origin)?.email
            onSignedIn?()
        } catch {
            message = error.localizedDescription
        }
    }

    func signIn(origin: URL?) async {
        guard let origin, !busy else { return }
        busy = true
        message = "Abriendo el inicio de sesión…"
        defer { busy = false }
        do {
            // Valid native credentials renew the web cookie silently; only their absence opens a window.
            let accessToken: String
            if let existing = try? await auth.accessToken(origin: origin) {
                accessToken = existing
                email = try await auth.current(origin: origin)?.email
            } else {
                let tokens = try await auth.signIn(origin: origin)
                accessToken = tokens.accessToken
                email = tokens.email
            }
            try await installWebSession(origin: origin, accessToken: accessToken)
            mode = .cognito
            logoutHost = await auth.knownConfig(origin: origin).flatMap { URL(string: $0.domain)?.host?.lowercased() }
            message = "Sesión iniciada."
            onSignedIn?()
        } catch {
            message = error.localizedDescription
        }
    }

    func signOut(origin: URL?) async {
        guard let origin, !busy else { return }
        busy = true
        defer { busy = false }
        await endWebSession(origin: origin)
        try? await auth.signOut(origin: origin)
        await webData.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast)
        email = nil
        message = "Sesión cerrada."
        onSignedOut?()
    }

    /// Trade the native token for the web view's cookie, scoped to this exact origin.
    private func installWebSession(origin: URL, accessToken: String) async throws {
        let url = origin.appendingPathComponent("auth/desktop-session")
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15)
        request.httpMethod = "POST"
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let session = URLSession(configuration: configuration, delegate: NoRedirects.shared, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        struct Exchange: Decodable {
            struct Cookie: Decodable { let name: String; let value: String; let max_age: Int }
            let cookie: Cookie
            let email: String?
        }
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.url == url, http.statusCode == 200,
              data.count <= 16 * 1024, let exchange = try? JSONDecoder().decode(Exchange.self, from: data),
              exchange.cookie.name == "voc_session", !exchange.cookie.value.isEmpty, let host = origin.host
        else { throw HandoffError("sign_in_failed") }
        var properties: [HTTPCookiePropertyKey: Any] = [
            .name: exchange.cookie.name, .value: exchange.cookie.value, .domain: host, .path: "/",
            .expires: Date().addingTimeInterval(TimeInterval(exchange.cookie.max_age)),
        ]
        if let port = origin.port { properties[.port] = String(port) }
        if origin.scheme == "https" { properties[.secure] = "TRUE" }
        guard let cookie = HTTPCookie(properties: properties) else { throw HandoffError("sign_in_failed") }
        await webData.httpCookieStore.setCookie(cookie)
        sessionCookie = exchange.cookie.value
    }

    /// Best effort: tell the server the web session is over; the local state is cleared regardless.
    private func endWebSession(origin: URL) async {
        guard let value = sessionCookie else { return }
        sessionCookie = nil
        var request = URLRequest(url: origin.appendingPathComponent("auth/logout"), timeoutInterval: 10)
        request.httpMethod = "POST"
        request.setValue("voc_session=\(value)", forHTTPHeaderField: "Cookie")
        request.setValue(origin.absoluteString, forHTTPHeaderField: "Origin")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let session = URLSession(configuration: configuration, delegate: NoRedirects.shared, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        _ = try? await session.data(for: request)
    }
}
