import Foundation
import MCP

/// Sessions belong to this listener; work identities belong to the shared SQLite store.
@MainActor
final class DesktopHTTPSessions {
    struct Reply {
        let response: HTTPResponse
        var lease: String? = nil
    }

    private struct Session {
        let server: Server
        let transport: StatefulHTTPServerTransport
        var touched = Date()
        var lease: String? = UUID().uuidString
        var requests = 1
    }

    private struct Identity: SessionIDGenerator {
        let value: String
        func generateSessionID() -> String { value }
    }

    private let store: HandoffStore
    private let opener: any HandoffOpening
    private var sessions: [String: Session] = [:]
    private var pending = 0
    private var stopped = false
    // Rotate sessions to bound the SDK's in-memory SSE replay history.
    private let requestLimit: Int
    private let sessionLimit: Int
    private let idleSeconds: TimeInterval
    private let validation = StandardValidationPipeline(validators: [
        AcceptHeaderValidator(mode: .sseRequired), ContentTypeValidator(),
        ProtocolVersionValidator(), SessionValidator(),
    ])

    init(store: HandoffStore, opener: any HandoffOpening, sessionLimit: Int = 16,
         requestLimit: Int = 128, idleSeconds: TimeInterval = 600) {
        self.store = store
        self.opener = opener
        self.sessionLimit = sessionLimit
        self.requestLimit = requestLimit
        self.idleSeconds = idleSeconds
    }

    func handle(_ request: HTTPRequest) async -> Reply {
        guard !stopped else { return Reply(response: DesktopHTTPPolicy.error(503, "Listener stopping")) }
        let identity = request.header(HTTPHeaderName.sessionID)
        if let identity {
            guard var session = sessions[identity] else {
                return Reply(response: DesktopHTTPPolicy.error(404, "Session expired; initialize again"))
            }
            guard session.lease == nil else { return Reply(response: DesktopHTTPPolicy.error(409, "Session request in progress")) }
            if session.requests >= requestLimit || Date().timeIntervalSince(session.touched) > idleSeconds {
                await close(identity)
                return Reply(response: DesktopHTTPPolicy.error(404, "Session expired; initialize again"))
            }
            let lease = UUID().uuidString
            session.lease = lease
            session.requests += 1
            session.touched = Date()
            sessions[identity] = session
            let response = await session.transport.handleRequest(request)
            if request.method == "DELETE", response.statusCode == 200 { await close(identity) }
            return Reply(response: response, lease: lease)
        }

        // Only initialize can allocate a session. The SDK still validates the JSON-RPC message.
        guard request.method == "POST", let body = request.body,
              let json = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              json["jsonrpc"] as? String == "2.0", json["method"] as? String == "initialize",
              json["id"] is String || json["id"] is NSNumber else {
            return Reply(response: DesktopHTTPPolicy.error(400, "Initialize first with a JSON-RPC request"))
        }
        let context = HTTPValidationContext(httpMethod: "POST", isInitializationRequest: true)
        if let error = validation.validate(request, context: context) { return Reply(response: error) }
        guard sessions.count + pending < sessionLimit else {
            return Reply(response: DesktopHTTPPolicy.error(503, "Session limit reached; close idle clients"))
        }
        pending += 1
        defer { pending -= 1 }
        let id = UUID().uuidString.lowercased()
        let transport = StatefulHTTPServerTransport(sessionIDGenerator: Identity(value: id), validationPipeline: validation)
        let server = await DesktopMCP.make(store: store, opener: opener)
        do {
            try await server.start(transport: transport)
            guard !stopped else {
                await server.stop()
                return Reply(response: DesktopHTTPPolicy.error(503, "Listener stopping"))
            }
            sessions[id] = Session(server: server, transport: transport)
            let response = await transport.handleRequest(request)
            if response.statusCode >= 400 { await close(id) }
            return Reply(response: response, lease: sessions[id]?.lease)
        } catch {
            await server.stop()
            return Reply(response: DesktopHTTPPolicy.error(500, "Could not initialize session"))
        }
    }

    func finish(_ lease: String?) {
        // A late socket cleanup cannot release a newer request on the same session.
        guard let lease, let identity = sessions.first(where: { $0.value.lease == lease })?.key else { return }
        sessions[identity]?.lease = nil
        sessions[identity]?.touched = Date()
    }

    func expireIdle() async {
        let expired = sessions.filter { $0.value.lease == nil && Date().timeIntervalSince($0.value.touched) > idleSeconds }.map(\.key)
        for id in expired { await close(id) }
    }

    private func close(_ identity: String) async {
        guard let session = sessions.removeValue(forKey: identity) else { return }
        await session.server.stop()
    }

    func stop() async {
        stopped = true
        for id in Array(sessions.keys) { await close(id) }
    }
}
