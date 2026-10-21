import CryptoKit
import Foundation
import Security

/// Nonsecret values the server publishes for a native sign-in against itself.
/// They are fetched from the configured origin, never typed or cached across servers.
struct AuthConfig: Codable, Equatable, Sendable {
    static let callbackURL = "voc-data-state://auth/callback"

    let issuer: String
    let domain: String
    let nativeClientID: String
    let scopes: [String]
    let callback: String
    let publicOrigin: String

    enum CodingKeys: String, CodingKey {
        case issuer, domain, scopes, callback
        case nativeClientID = "native_client_id"
        case publicOrigin = "public_origin"
    }

    func validate(origin: URL) throws {
        guard let issuerURL = URL(string: issuer), issuerURL.scheme == "https",
              let issuerHost = issuerURL.host, issuerHost.hasPrefix("cognito-idp."),
              issuerHost.hasSuffix(".amazonaws.com"), issuerURL.path.count > 1,
              let domainURL = URL(string: domain), domainURL.scheme == "https",
              domainURL.host != nil, domainURL.path.isEmpty || domainURL.path == "/",
              domainURL.query == nil, domainURL.fragment == nil,
              callback == Self.callbackURL, !nativeClientID.isEmpty,
              scopes.contains("openid"), scopes.contains("voc-api/access"),
              scopes.allSatisfy({ !$0.isEmpty && !$0.contains(where: \.isWhitespace) })
        else { throw HandoffError("invalid_auth_config") }
        // The server names the address people use; the client must be talking to that address.
        guard let expected = try? ConnectionProfile(server: publicOrigin).origin, expected == origin else {
            throw HandoffError("origin_mismatch")
        }
    }
}

enum PKCE {
    /// Random bytes encoded as base64url: only unreserved characters, 43 chars for 32 bytes.
    static func random(bytes count: Int = 32) throws -> String {
        var bytes = [UInt8](repeating: 0, count: count)
        guard SecRandomCopyBytes(kSecRandomDefault, count, &bytes) == errSecSuccess else {
            throw HandoffError("sign_in_failed")
        }
        return Data(bytes).base64URLEncodedString()
    }

    static func challenge(for verifier: String) -> String {
        Data(SHA256.hash(data: Data(verifier.utf8))).base64URLEncodedString()
    }
}

extension Data {
    func base64URLEncodedString() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    init?(base64URLEncoded text: String) {
        var value = text.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while value.count % 4 != 0 { value.append("=") }
        self.init(base64Encoded: value)
    }
}

/// One sign-in in flight: its state, nonce and verifier exist only for this attempt.
struct AuthAttempt: Equatable, Sendable {
    let origin: URL
    let config: AuthConfig
    let state: String
    let nonce: String
    let verifier: String

    init(origin: URL, config: AuthConfig) throws {
        try config.validate(origin: origin)
        self.origin = origin
        self.config = config
        state = try PKCE.random()
        nonce = try PKCE.random()
        verifier = try PKCE.random(bytes: 48)
    }

    var authorizeURL: URL {
        var parts = URLComponents(string: config.domain)!
        parts.path = "/oauth2/authorize"
        parts.queryItems = [
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "client_id", value: config.nativeClientID),
            URLQueryItem(name: "redirect_uri", value: config.callback),
            URLQueryItem(name: "scope", value: config.scopes.joined(separator: " ")),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "nonce", value: nonce),
            URLQueryItem(name: "code_challenge", value: PKCE.challenge(for: verifier)),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
        ]
        return parts.url!
    }

    /// Accept only the exact callback route carrying this attempt's state.
    func code(from callback: URL) throws -> String {
        guard let parts = URLComponents(url: callback, resolvingAgainstBaseURL: false),
              parts.scheme == "voc-data-state", parts.host == "auth", parts.path == "/callback",
              parts.user == nil, parts.password == nil, parts.port == nil, parts.fragment == nil
        else { throw HandoffError("auth_callback_rejected") }
        var values: [String: String] = [:]
        for item in parts.queryItems ?? [] {
            guard values[item.name] == nil, let value = item.value else { throw HandoffError("auth_callback_rejected") }
            values[item.name] = value
        }
        guard let returned = values["state"], constantTimeEqual(returned, state) else {
            throw HandoffError("auth_callback_rejected")
        }
        if let error = values["error"] {
            throw HandoffError(error == "access_denied" ? "sign_in_cancelled" : "sign_in_failed")
        }
        guard let code = values["code"], !code.isEmpty, code.count <= 2048,
              code.allSatisfy({ $0.isLetter || $0.isNumber || "-._~".contains($0) })
        else { throw HandoffError("auth_callback_rejected") }
        return code
    }
}

func constantTimeEqual(_ left: String, _ right: String) -> Bool {
    let a = Array(left.utf8), b = Array(right.utf8)
    guard a.count == b.count else { return false }
    var difference: UInt8 = 0
    for index in a.indices { difference |= a[index] ^ b[index] }
    return difference == 0
}

/// Renewable credentials for one server origin and one person.
struct TokenSet: Codable, Equatable, Sendable {
    let origin: String
    let issuer: String
    let subject: String
    let email: String?
    var accessToken: String
    var refreshToken: String?
    var expiresAt: Double

    func isFresh(at now: Date = Date(), margin: TimeInterval = 60) -> Bool {
        expiresAt - now.timeIntervalSince1970 > margin
    }
}

protocol CredentialStore: Sendable {
    func load(origin: String) throws -> TokenSet?
    func save(_ tokens: TokenSet) throws
    func delete(origin: String) throws
}

/// Keychain items keyed by server origin; access tokens never reach UserDefaults or files.
final class KeychainCredentialStore: CredentialStore {
    private let service: String

    init(service: String = "dev.voc.data-state.auth") { self.service = service }

    private func query(origin: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: origin]
    }

    func load(origin: String) throws -> TokenSet? {
        var item: CFTypeRef?
        var request = query(origin: origin)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw HandoffError("credential_store_failed") }
        return try? JSONDecoder().decode(TokenSet.self, from: data)
    }

    func save(_ tokens: TokenSet) throws {
        let data = try JSONEncoder().encode(tokens)
        let attributes: [String: Any] = [kSecValueData as String: data,
                                         kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query(origin: tokens.origin) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let created = query(origin: tokens.origin).merging(attributes) { _, new in new }
            guard SecItemAdd(created as CFDictionary, nil) == errSecSuccess else { throw HandoffError("credential_store_failed") }
        } else if status != errSecSuccess {
            throw HandoffError("credential_store_failed")
        }
    }

    func delete(origin: String) throws {
        let status = SecItemDelete(query(origin: origin) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw HandoffError("credential_store_failed") }
    }
}

final class MemoryCredentialStore: CredentialStore, @unchecked Sendable {
    private let lock = NSLock()
    private var items: [String: TokenSet] = [:]
    func load(origin: String) throws -> TokenSet? { lock.lock(); defer { lock.unlock() }; return items[origin] }
    func save(_ tokens: TokenSet) throws { lock.lock(); defer { lock.unlock() }; items[tokens.origin] = tokens }
    func delete(origin: String) throws { lock.lock(); defer { lock.unlock() }; items[origin] = nil }
}

/// Claims read from a token the client itself requested. The server verifies signatures;
/// the client checks that the response belongs to its own attempt and issuer.
struct TokenClaims {
    let values: [String: Any]

    init(token: String) throws {
        let segments = token.split(separator: ".", omittingEmptySubsequences: false)
        guard segments.count == 3, let payload = Data(base64URLEncoded: String(segments[1])),
              let object = try? JSONSerialization.jsonObject(with: payload), let values = object as? [String: Any]
        else { throw HandoffError("sign_in_failed") }
        self.values = values
    }

    func string(_ name: String) -> String? { values[name] as? String }
    var expiry: Double? { (values["exp"] as? NSNumber)?.doubleValue }
}

/// The public-client exchange at Cognito's token endpoint: no secret, PKCE instead.
final class TokenExchange: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let configuration: URLSessionConfiguration

    init(configuration: URLSessionConfiguration = .ephemeral) {
        self.configuration = configuration
    }

    struct Response: Decodable {
        let access_token: String
        let id_token: String?
        let refresh_token: String?
        let expires_in: Double
    }

    func exchange(code: String, attempt: AuthAttempt) async throws -> TokenSet {
        let response = try await post(domain: attempt.config.domain, form: [
            "grant_type": "authorization_code", "client_id": attempt.config.nativeClientID,
            "code": code, "redirect_uri": attempt.config.callback, "code_verifier": attempt.verifier,
        ])
        guard let idToken = response.id_token else { throw HandoffError("sign_in_failed") }
        let identity = try TokenClaims(token: idToken)
        let now = Date().timeIntervalSince1970
        guard identity.string("iss") == attempt.config.issuer, identity.string("aud") == attempt.config.nativeClientID,
              identity.string("token_use") == "id", let subject = identity.string("sub"), !subject.isEmpty,
              let nonce = identity.string("nonce"), constantTimeEqual(nonce, attempt.nonce),
              let expiry = identity.expiry, expiry > now - 30
        else { throw HandoffError("sign_in_failed") }
        try Self.check(accessToken: response.access_token, config: attempt.config, subject: subject)
        return TokenSet(origin: attempt.origin.absoluteString, issuer: attempt.config.issuer, subject: subject,
                        email: identity.string("email"), accessToken: response.access_token,
                        refreshToken: response.refresh_token, expiresAt: now + response.expires_in)
    }

    func refresh(_ tokens: TokenSet, config: AuthConfig) async throws -> TokenSet {
        guard let refreshToken = tokens.refreshToken else { throw HandoffError("sign_in_required") }
        let response = try await post(domain: config.domain, form: [
            "grant_type": "refresh_token", "client_id": config.nativeClientID, "refresh_token": refreshToken,
        ])
        try Self.check(accessToken: response.access_token, config: config, subject: tokens.subject)
        var renewed = tokens
        renewed.accessToken = response.access_token
        renewed.refreshToken = response.refresh_token ?? tokens.refreshToken
        renewed.expiresAt = Date().timeIntervalSince1970 + response.expires_in
        return renewed
    }

    private static func check(accessToken: String, config: AuthConfig, subject: String) throws {
        let claims = try TokenClaims(token: accessToken)
        guard claims.string("iss") == config.issuer, claims.string("client_id") == config.nativeClientID,
              claims.string("token_use") == "access", claims.string("sub") == subject,
              (claims.string("scope") ?? "").split(separator: " ").contains("voc-api/access")
        else { throw HandoffError("sign_in_failed") }
    }

    private func post(domain: String, form: [String: String]) async throws -> Response {
        var parts = URLComponents(string: domain)!
        parts.path = "/oauth2/token"
        let url = parts.url!
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        var body = URLComponents()
        body.queryItems = form.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        request.httpBody = Data((body.percentEncodedQuery ?? "").utf8)
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, http.url == url, data.count <= 64 * 1024 else {
                throw HandoffError("sign_in_failed")
            }
            guard http.statusCode == 200 else { throw HandoffError("sign_in_failed") }
            return try JSONDecoder().decode(Response.self, from: data)
        } catch let error as HandoffError { throw error }
        catch { throw HandoffError("sign_in_failed") }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // A token endpoint that redirects is not the one we were configured with.
        completionHandler(nil)
    }
}

/// Whatever shows the login page; the app uses ASWebAuthenticationSession, tests a fake.
protocol SignInSession: Sendable {
    func authenticate(_ url: URL, callbackScheme: String) async throws -> URL
}

/// Owns the sign-in state for one or more server origins.
actor NativeAuth {
    private let store: any CredentialStore
    private let exchange: TokenExchange
    private let session: any SignInSession
    private let configuration: URLSessionConfiguration
    private var loginOffered: [String: Bool] = [:]
    private var configs: [String: AuthConfig] = [:]

    init(store: any CredentialStore, session: any SignInSession, configuration: URLSessionConfiguration = .ephemeral) {
        self.store = store
        self.session = session
        self.configuration = configuration
        exchange = TokenExchange(configuration: configuration)
    }

    /// `GET <origin>/auth/config`: the server says which pool and client to use with it.
    func config(for origin: URL) async throws -> AuthConfig {
        let url = origin.appendingPathComponent("auth/config")
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let session = URLSession(configuration: configuration, delegate: NoRedirects.shared, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let data: Data, response: URLResponse
        do { (data, response) = try await session.data(for: request) } catch { throw HandoffError("auth_config_unavailable") }
        guard let http = response as? HTTPURLResponse, http.url == url, data.count <= 16 * 1024 else {
            throw HandoffError("auth_config_unavailable")
        }
        guard http.statusCode == 200 else {
            throw HandoffError(http.statusCode == 404 ? "server_without_login" : "auth_config_unavailable")
        }
        guard let config = try? JSONDecoder().decode(AuthConfig.self, from: data) else {
            throw HandoffError("invalid_auth_config")
        }
        try config.validate(origin: origin)
        configs[origin.absoluteString] = config
        return config
    }

    /// The last configuration read for this origin, if any; no network.
    func knownConfig(origin: URL) -> AuthConfig? {
        configs[origin.absoluteString]
    }

    /// Whether this server runs in cognito mode; a local-mode server is remembered as such.
    func hasLogin(origin: URL) async throws -> Bool {
        if let known = loginOffered[origin.absoluteString] { return known }
        do {
            _ = try await config(for: origin)
            loginOffered[origin.absoluteString] = true
            return true
        } catch let error as HandoffError where error.code == "server_without_login" {
            loginOffered[origin.absoluteString] = false
            return false
        }
    }

    /// The token native requests attach, or nil for a server without login.
    func bearer(origin: String) async throws -> String? {
        let url = try ConnectionProfile(server: origin).origin
        guard try await hasLogin(origin: url) else { return nil }
        return try await accessToken(origin: url)
    }

    func signIn(origin: URL) async throws -> TokenSet {
        let attempt = try AuthAttempt(origin: origin, config: try await config(for: origin))
        let callback: URL
        do { callback = try await session.authenticate(attempt.authorizeURL, callbackScheme: "voc-data-state") }
        catch let error as HandoffError { throw error }
        catch { throw HandoffError("sign_in_cancelled") }
        let code = try attempt.code(from: callback)
        let tokens = try await exchange.exchange(code: code, attempt: attempt)
        try store.save(tokens)
        return tokens
    }

    func current(origin: URL) throws -> TokenSet? {
        try store.load(origin: origin.absoluteString)
    }

    /// A fresh access token, renewed once when needed; otherwise the person signs in again.
    func accessToken(origin: URL) async throws -> String {
        guard let tokens = try store.load(origin: origin.absoluteString) else { throw HandoffError("sign_in_required") }
        if tokens.isFresh() { return tokens.accessToken }
        do {
            let config = try await config(for: origin)
            guard config.issuer == tokens.issuer else { throw HandoffError("sign_in_required") }
            let renewed = try await exchange.refresh(tokens, config: config)
            try store.save(renewed)
            return renewed.accessToken
        } catch {
            try? store.delete(origin: origin.absoluteString)
            throw HandoffError("sign_in_required")
        }
    }

    func signOut(origin: URL) throws {
        try store.delete(origin: origin.absoluteString)
    }
}

final class NoRedirects: NSObject, URLSessionTaskDelegate {
    static let shared = NoRedirects()
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
