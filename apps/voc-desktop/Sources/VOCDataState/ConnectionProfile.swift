import Foundation

/// The native client selects a server; the server owns publications and conversations.
struct ConnectionProfile: Equatable {
    let origin: URL
    let conversationID: String?

    init(server: String, conversation: String = "") throws {
        let text = server.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.contains("\\"),
              !text.contains(where: { $0.isWhitespace }),
              var parts = URLComponents(string: text),
              let scheme = parts.scheme?.lowercased(),
              let host = parts.host?.lowercased(),
              !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              parts.port.map({ (1...65535).contains($0) }) ?? true
        else {
            throw ConnectionError.invalidServer
        }
        let loopback = ["localhost", "127.0.0.1", "[::1]", "::1"].contains(host)
        guard scheme == "https" || (scheme == "http" && loopback) else {
            throw ConnectionError.insecureServer
        }
        // Normalize the origin so the default port and host casing compare equally.
        parts.scheme = scheme
        parts.host = host
        parts.path = ""
        if parts.port == (scheme == "https" ? 443 : 80) { parts.port = nil }
        guard let origin = parts.url else { throw ConnectionError.invalidServer }
        self.origin = origin

        let identity = conversation.trimmingCharacters(in: .whitespacesAndNewlines)
        if identity.isEmpty {
            conversationID = nil
        } else if let uuid = UUID(uuidString: identity),
                  uuid.uuidString.lowercased() == identity.lowercased() {
            conversationID = uuid.uuidString.lowercased()
        } else {
            throw ConnectionError.invalidConversation
        }
    }

    var workspaceURL: URL {
        var parts = URLComponents(url: origin, resolvingAgainstBaseURL: false)!
        parts.path = "/"
        if let conversationID {
            parts.queryItems = [URLQueryItem(name: "conversation", value: conversationID)]
        }
        return parts.url!
    }

    func contains(_ url: URL) -> Bool {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.user == nil, parts.password == nil,
              let scheme = parts.scheme?.lowercased(),
              let host = parts.host?.lowercased()
        else { return false }
        let expectedPort = origin.port ?? (origin.scheme == "https" ? 443 : 80)
        let actualPort = parts.port ?? (scheme == "https" ? 443 : 80)
        // URL.host and URLComponents.host differ in IPv6 bracket formatting.
        let expectedHost = URLComponents(url: origin, resolvingAgainstBaseURL: false)?.host?.lowercased()
        return scheme == origin.scheme && host == expectedHost && actualPort == expectedPort
    }

    func navigation(to url: URL?, userActivated: Bool) -> NavigationDecision {
        guard let url,
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.user == nil, parts.password == nil,
              let host = parts.host, !host.isEmpty,
              ["http", "https"].contains(parts.scheme?.lowercased() ?? "")
        else { return .block }
        if contains(url) { return .allow }
        // Redirects cannot silently leave the server chosen by the user.
        return userActivated ? .external : .block
    }
}

enum NavigationDecision: Equatable { case allow, external, block }

enum ConnectionError: LocalizedError {
    case invalidServer, insecureServer, invalidConversation

    var errorDescription: String? {
        switch self {
        case .invalidServer:
            return "Escribe solo el origen del servidor, por ejemplo http://127.0.0.1:8091, sin rutas, credenciales ni parámetros."
        case .insecureServer:
            return "Usa HTTPS para servidores externos. HTTP solo se admite para localhost, 127.0.0.1 o ::1."
        case .invalidConversation:
            return "La conversación debe ser un UUID válido; deja el campo vacío para comenzar otra."
        }
    }
}

struct WebCommand: Equatable {
    enum Action: Equatable { case connect, reload }
    let id = UUID()
    let profile: ConnectionProfile
    let action: Action
}

/// SwiftUI can redraw freely; only an explicit command requests another navigation.
struct NavigationTracker {
    private(set) var lastCommand: UUID?

    mutating func target(for command: WebCommand, currentURL: URL?) -> URL? {
        guard lastCommand != command.id else { return nil }
        lastCommand = command.id
        if command.action == .reload, let currentURL, command.profile.contains(currentURL) {
            // The web app adds its conversation ID with history.replaceState.
            return currentURL
        }
        return command.profile.workspaceURL
    }
}
