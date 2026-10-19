import Foundation
import MCP

/// A development-only listener: no browser access, remote bind address or caller-selected origin.
struct DesktopHTTPPolicy: Sendable {
    static let port = 8766
    static let bodyLimit = 16 * 1024
    private let credential: [UInt8]
    let port: Int

    init(token: String?, port: Int = Self.port) throws {
        guard let token, (32...512).contains(token.utf8.count),
              token.utf8.allSatisfy({ (33...126).contains($0) }) else {
            throw HandoffError("http_token_required")
        }
        credential = Array("Bearer \(token)".utf8)
        self.port = port
    }

    func rejection(_ request: HTTPRequest) -> HTTPResponse? {
        // Authenticate before routing, including unknown paths, sessions and DELETE.
        let received = Array((request.header("Authorization") ?? "").utf8)
        var difference = received.count ^ credential.count
        for index in credential.indices {
            difference |= Int(credential[index] ^ (index < received.count ? received[index] : 0))
        }
        guard difference == 0 else {
            return .error(statusCode: 401, .invalidRequest("Unauthorized"),
                          extraHeaders: ["WWW-Authenticate": "Bearer realm=\"voc-desktop\""])
        }
        let hosts = ["127.0.0.1:\(port)", "localhost:\(port)", "host.docker.internal:\(port)"]
        guard let host = request.header("Host"), hosts.contains(host.lowercased()),
              request.header("Origin") == nil else {
            return Self.error(403, "Native clients only; Host or Origin rejected")
        }
        guard request.path == "/mcp" else { return Self.error(404, "Not Found") }
        guard ["POST", "DELETE"].contains(request.method) else {
            // This adapter has no server-initiated events or resumable GET stream.
            return .error(statusCode: 405, .invalidRequest("Method Not Allowed"),
                          extraHeaders: ["Allow": "POST, DELETE"])
        }
        if (request.body?.count ?? 0) > Self.bodyLimit { return Self.error(413, "Request too large") }
        return nil
    }

    static func error(_ status: Int, _ message: String) -> HTTPResponse {
        .error(statusCode: status, .invalidRequest(message))
    }
}
