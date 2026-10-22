import XCTest
import MCP

/// Real loopback HTTP, disposable SQLite and a fake opener: no test opens the user's app.
@MainActor
final class DesktopHTTPTests: XCTestCase {
    private let token = String(repeating: "test-token-", count: 4)

    struct Running {
        let directory: URL
        let store: HandoffStore
        let opener: DesktopMCPTests.Opener
        let server: DesktopHTTPServer
        let port: Int
        var url: URL { URL(string: "http://127.0.0.1:\(port)/mcp")! }
        func stop() async {
            await server.stop()
            try? FileManager.default.removeItem(at: directory)
        }
    }

    func start(sessionLimit: Int = 16, requestLimit: Int = 128) async throws -> Running {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = try HandoffStore(directory: directory)
        try store.configure(origin: "http://127.0.0.1:8091")
        let opener = DesktopMCPTests.Opener()
        let server = try DesktopHTTPServer(token: token, store: store, opener: opener,
                                          sessionLimit: sessionLimit, requestLimit: requestLimit)
        let port = try await server.start(port: 0)
        return Running(directory: directory, store: store, opener: opener, server: server, port: port)
    }

    func request(_ running: Running, method: String = "POST", session: String? = nil,
                 rpc: [String: Any]? = nil, headers: [String: String] = [:],
                 authorization: Bool = true, body: Data? = nil) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: running.url, timeoutInterval: 5)
        request.httpMethod = method
        request.setValue("application/json, text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("2025-11-25", forHTTPHeaderField: "MCP-Protocol-Version")
        if authorization { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let session { request.setValue(session, forHTTPHeaderField: "Mcp-Session-Id") }
        for (name, value) in headers { request.setValue(value, forHTTPHeaderField: name) }
        if let rpc { request.httpBody = try JSONSerialization.data(withJSONObject: rpc) }
        else { request.httpBody = body }
        let (data, response) = try await URLSession.shared.data(for: request)
        return (data, try XCTUnwrap(response as? HTTPURLResponse))
    }

    var initialize: [String: Any] {
        ["jsonrpc": "2.0", "id": 1, "method": "initialize", "params": [
            "protocolVersion": "2025-11-25", "capabilities": [:],
            "clientInfo": ["name": "native-http-test", "version": "1"],
        ]]
    }

    func initialized(_ running: Running) async throws -> String {
        let (data, response) = try await request(running, rpc: initialize)
        XCTAssertEqual(response.statusCode, 200, String(decoding: data, as: UTF8.self))
        let identity = try XCTUnwrap(response.value(forHTTPHeaderField: "Mcp-Session-Id"))
        let (_, acknowledged) = try await request(running, session: identity,
                                                  rpc: ["jsonrpc": "2.0", "method": "notifications/initialized"])
        XCTAssertEqual(acknowledged.statusCode, 202)
        return identity
    }

    func testTokenValidationAndEdgePolicyFailClosed() throws {
        for token in [nil, "", "short", String(repeating: "a", count: 513), String(repeating: "a", count: 32) + "\n"] {
            XCTAssertThrowsError(try DesktopHTTPPolicy(token: token))
        }
        let policy = try DesktopHTTPPolicy(token: token)
        let headers = ["Authorization": "Bearer \(token)", "Host": "127.0.0.1:8766"]
        XCTAssertNil(policy.rejection(HTTPRequest(method: "POST", headers: headers, path: "/mcp")))
        for host in ["localhost:8766", "host.docker.internal:8766"] {
            XCTAssertNil(policy.rejection(HTTPRequest(method: "POST", headers: headers.merging(["Host": host]) { _, new in new }, path: "/mcp")))
        }
        for host in ["evil.example:8766", "localhost.evil.example:8766", "127.0.0.1:8765", "localhost", ""] {
            let request = HTTPRequest(method: "POST", headers: headers.merging(["Host": host]) { _, new in new }, path: "/mcp")
            XCTAssertEqual(policy.rejection(request)?.statusCode, 403)
        }
        for origin in ["https://evil.example", "http://127.0.0.1:8766", "null", ""] {
            let request = HTTPRequest(method: "POST", headers: headers.merging(["Origin": origin]) { _, new in new }, path: "/mcp")
            XCTAssertEqual(policy.rejection(request)?.statusCode, 403)
        }
        XCTAssertEqual(policy.rejection(HTTPRequest(method: "POST", headers: headers, path: "/mcp?token=hidden"))?.statusCode, 404)
    }

    func testHTTPRejectsBeforeOpeningOrAllocatingSessions() async throws {
        let running = try await start(sessionLimit: 1)
        for method in ["POST", "GET", "DELETE"] {
            let (data, response) = try await request(running, method: method, session: "unknown", authorization: false)
            XCTAssertEqual(response.statusCode, 401)
            XCTAssertFalse(String(decoding: data, as: UTF8.self).contains(token))
        }
        for headers in [["Authorization": "Bearer wrong"], ["Origin": "https://evil.example"], ["Host": "evil.example:8766"]] {
            let (_, response) = try await request(running, rpc: initialize, headers: headers)
            XCTAssertEqual(response.statusCode, headers["Authorization"] == nil ? 403 : 401)
        }
        let (_, tooLarge) = try await request(running, body: Data(repeating: 65, count: DesktopHTTPPolicy.bodyLimit + 1))
        XCTAssertEqual(tooLarge.statusCode, 413)
        let (_, invalid) = try await request(running, body: Data("not json".utf8))
        XCTAssertEqual(invalid.statusCode, 400)
        let (_, content) = try await request(running, rpc: initialize, headers: ["Content-Type": "text/plain"])
        XCTAssertEqual(content.statusCode, 415)
        let (_, accept) = try await request(running, rpc: initialize, headers: ["Accept": "application/json"])
        XCTAssertEqual(accept.statusCode, 406)
        let id = try await initialized(running) // Invalid requests did not consume the only session slot.
        let (_, unknown) = try await request(running, session: "missing", rpc: initialize)
        XCTAssertEqual(unknown.statusCode, 404)
        let (_, get) = try await request(running, method: "GET", session: id)
        XCTAssertEqual(get.statusCode, 405)
        XCTAssertEqual(running.opener.calls, 0)
        await running.stop()
    }

    func testChunkedBodyIsBoundedWithoutContentLength() async throws {
        let running = try await start()
        var request = URLRequest(url: running.url, timeoutInterval: 5)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBodyStream = InputStream(data: Data(repeating: 65, count: DesktopHTTPPolicy.bodyLimit + 1))
        let (_, response) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 413)
        XCTAssertEqual(running.opener.calls, 0)
        await running.stop()
    }

    func testLateSocketCleanupCannotReleaseTheNextSessionRequest() async throws {
        let running = try await start()
        let router = DesktopHTTPSessions(store: running.store, opener: running.opener)
        let headers = ["Accept": "application/json, text/event-stream", "Content-Type": "application/json"]
        let opened = await router.handle(HTTPRequest(method: "POST", headers: headers,
                                                     body: try JSONSerialization.data(withJSONObject: initialize), path: "/mcp"))
        if case .stream(let stream, _) = opened.response { for try await _ in stream {} }
        let identity = try XCTUnwrap(opened.response.headers[HTTPHeaderName.sessionID])
        router.finish(opened.lease)
        let ready = HTTPRequest(method: "POST", headers: headers.merging(["Mcp-Session-Id": identity]) { _, new in new },
                                body: Data("{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}".utf8), path: "/mcp")
        let second = await router.handle(ready)
        XCTAssertEqual(second.response.statusCode, 202)
        router.finish(opened.lease) // The old connection closes after the next request has begun.
        let concurrent = await router.handle(ready)
        XCTAssertEqual(concurrent.response.statusCode, 409)
        router.finish(second.lease)
        let after = await router.handle(ready)
        XCTAssertEqual(after.response.statusCode, 202)
        router.finish(after.lease)
        await router.stop()
        await running.stop()
    }

    func testIdleSessionExpiresWithoutAffectingSavedWork() async throws {
        let running = try await start()
        let router = DesktopHTTPSessions(store: running.store, opener: running.opener, idleSeconds: -1)
        let request = HTTPRequest(method: "POST", headers: [
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
        ], body: try JSONSerialization.data(withJSONObject: initialize), path: "/mcp")
        let reply = await router.handle(request)
        if case .stream(let stream, _) = reply.response { for try await _ in stream {} }
        let session = try XCTUnwrap(reply.response.headers[HTTPHeaderName.sessionID])
        router.finish(reply.lease)
        let work = try running.store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")).work
        await router.expireIdle()
        let expired = await router.handle(HTTPRequest(method: "DELETE", headers: ["Mcp-Session-Id": session], path: "/mcp"))
        XCTAssertEqual(expired.response.statusCode, 404)
        XCTAssertEqual(try running.store.read(work.workID), work)
        await router.stop()
        await running.stop()
    }

    func testSessionAdmissionRotationAndDeleteAreBounded() async throws {
        let running = try await start(sessionLimit: 1, requestLimit: 3)
        let id = try await initialized(running) // initialize + initialized count as two requests.
        let (_, full) = try await request(running, rpc: initialize)
        XCTAssertEqual(full.statusCode, 503)
        let list: [String: Any] = ["jsonrpc": "2.0", "id": 2, "method": "tools/list"]
        let (_, listed) = try await request(running, session: id, rpc: list)
        XCTAssertEqual(listed.statusCode, 200)
        let (_, expired) = try await request(running, session: id, rpc: list)
        XCTAssertEqual(expired.statusCode, 404)
        let next = try await initialized(running)
        XCTAssertNotEqual(id, next)
        let (_, deleted) = try await request(running, method: "DELETE", session: next)
        XCTAssertEqual(deleted.statusCode, 200)
        let (_, gone) = try await request(running, session: next, rpc: list)
        XCTAssertEqual(gone.statusCode, 404)
        _ = try await initialized(running)
        await running.stop()
    }

    func client(_ running: Running) async throws -> Client {
        let client = Client(name: "swift-http-client", version: "1")
        let token = token
        let transport = HTTPClientTransport(endpoint: running.url, streaming: false, requestModifier: { request in
            var request = request
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            request.timeoutInterval = 5
            return request
        })
        _ = try await client.connect(transport: transport)
        return client
    }

    func call(_ client: Client, _ name: String, _ arguments: [String: Value]) async throws -> CallTool.Result {
        let context = try await client.send(CallTool.request(.init(name: name, arguments: arguments)))
        return try await context.value
    }

    func testOfficialHTTPClientReplaySharesStateWithAnotherTransportAndRestart() async throws {
        let running = try await start()
        let client = try await client(running)
        let (tools, _) = try await client.listTools()
        XCTAssertEqual(tools.map(\.name), ["voc_open", "voc_read_result"])
        let arguments: [String: Value] = ["request_id": .string(UUID().uuidString.lowercased()), "question": "Fuentes"]
        let first = try await call(client, "voc_open", arguments)
        let id = try XCTUnwrap(first.structuredContent?.objectValue?["work_id"]?.stringValue)
        XCTAssertEqual(try running.store.read(id).presentation, .http)
        // A second server instance (as stdio uses) opens the same database, not a new work registry.
        let other = try await DesktopMCPTests().session(HandoffStore(directory: running.directory), running.opener)
        let replay = try await other.call("voc_open", arguments)
        XCTAssertEqual(first.structuredContent, replay.structuredContent)
        XCTAssertEqual(running.opener.calls, 1)
        let conflict = try await call(client, "voc_open", ["request_id": arguments["request_id"]!, "question": "Otra"])
        XCTAssertEqual(conflict.structuredContent?.objectValue?["error_code"], "request_conflict")
        let next = try await call(client, "voc_open", ["request_id": .string(UUID().uuidString.lowercased()), "question": "Fuentes"])
        XCTAssertEqual(next.structuredContent?.objectValue?["opening_status"], "opening")
        XCTAssertEqual(running.opener.calls, 2)
        XCTAssertFalse(try running.store.read(id).active)
        do {
            _ = try await call(client, "voc_read_result", ["work_id": .string(id), "command": "anything"])
            XCTFail("Accepted an undocumented argument")
        } catch let error as MCPError {
            guard case .invalidParams = error else { throw error }
        }
        await client.disconnect()
        await running.server.stop()
        // The superseded work is still readable, only its admission flag changed.
        let saved = try await other.call("voc_read_result", ["work_id": .string(id)])
        XCTAssertEqual(saved.structuredContent?.objectValue?["work_id"], .string(id))
        XCTAssertEqual(saved.structuredContent?.objectValue?["opening_status"], "opening")
        XCTAssertEqual(saved.structuredContent?.objectValue?["active"], false)
        let restarted = try DesktopHTTPServer(token: token, store: running.store, opener: running.opener)
        let port = try await restarted.start(port: running.port)
        let link = Running(directory: running.directory, store: running.store, opener: running.opener, server: restarted, port: port)
        let reconnected = try await self.client(link)
        let again = try await call(reconnected, "voc_open", arguments)
        XCTAssertEqual(again.structuredContent?.objectValue?["work_id"], .string(id))
        XCTAssertEqual(again.structuredContent?.objectValue?["active"], false)
        XCTAssertEqual(running.opener.calls, 2)
        await reconnected.disconnect()
        await other.close()
        await link.stop()
    }

    func testPortCollisionDoesNotStopExistingListener() async throws {
        let running = try await start()
        let second = try DesktopHTTPServer(token: token, store: running.store, opener: running.opener)
        do { _ = try await second.start(port: running.port); XCTFail("Allowed a second bind") }
        catch let error as HandoffError { XCTAssertEqual(error.code, "http_bind_failed") }
        _ = try await initialized(running)
        await running.stop()
    }

    func testBundledHTTPHelperWithoutTokenFailsWithoutProtocolOutput() async throws {
        let process = Process(), output = Pipe(), errors = Pipe()
        process.executableURL = try DesktopStdioTests().helper()
        process.arguments = ["--http"]
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "VOC_DESKTOP_MCP_TOKEN")
        process.environment = environment
        process.standardOutput = output
        process.standardError = errors
        let exited = expectation(description: "Missing credential fails promptly")
        process.terminationHandler = { _ in exited.fulfill() }
        try process.run()
        defer { if process.isRunning { process.terminate() } }
        await fulfillment(of: [exited], timeout: 5)
        guard !process.isRunning else { return }
        XCTAssertEqual(process.terminationStatus, 1)
        try output.fileHandleForWriting.close()
        try errors.fileHandleForWriting.close()
        XCTAssertEqual(try output.fileHandleForReading.readToEnd() ?? Data(), Data())
        let error = String(decoding: try errors.fileHandleForReading.readToEnd() ?? Data(), as: UTF8.self)
        XCTAssertTrue(error.contains("http_token_required"))
    }
}
