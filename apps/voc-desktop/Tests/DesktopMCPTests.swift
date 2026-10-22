import XCTest
import MCP

/// Test the public protocol using disposable state and an opener without desktop effects.
@MainActor
final class DesktopMCPTests: XCTestCase {
    final class Opener: HandoffOpening {
        var calls = 0
        var fail = false
        func open(_ work: HandoffWork) async throws {
            calls += 1
            if fail { throw HandoffError("launch_failed") }
        }
    }

    struct Session {
        let client: Client
        let server: Server
        let initialized: Initialize.Result

        func close() async {
            await client.disconnect()
            await server.stop()
        }

        func call(_ name: String, _ arguments: [String: Value]) async throws -> CallTool.Result {
            let context = try await client.send(CallTool.request(.init(name: name, arguments: arguments)))
            return try await context.value
        }
    }

    func session(_ store: HandoffStore, _ opener: Opener, reports: any HandoffReportTransport = HandoffReportClient()) async throws -> Session {
        let transports = await InMemoryTransport.createConnectedPair()
        let server = await DesktopMCP.make(store: store, opener: opener, reports: reports)
        try await server.start(transport: transports.server)
        let client = Client(name: "voc-native-test", version: "1")
        let initialized = try await client.connect(transport: transports.client)
        return Session(client: client, server: server, initialized: initialized)
    }

    func state() throws -> (URL, HandoffStore) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = try HandoffStore(directory: directory)
        try store.configure(origin: "http://127.0.0.1:8091")
        return (directory, store)
    }

    func payload(_ result: CallTool.Result) throws -> [String: Value] {
        // Text and structured responses must agree, including the protocol error flag.
        let value = try XCTUnwrap(result.structuredContent)
        guard case .text(let text, _, _) = try XCTUnwrap(result.content.first) else {
            throw HandoffError("invalid_response")
        }
        XCTAssertEqual(try JSONDecoder().decode(Value.self, from: Data(text.utf8)), value)
        let object = try XCTUnwrap(value.objectValue)
        XCTAssertEqual(object["ok"], .bool(result.isError != true))
        return object
    }

    func testDiscoveryHasNoOpeningAndAdvertisesOnlyTwoStrictTools() async throws {
        let (directory, store) = try state()
        defer { try? FileManager.default.removeItem(at: directory) }
        let opener = Opener()
        let link = try await session(store, opener)
        let (tools, cursor) = try await link.client.listTools()
        XCTAssertEqual(tools.map(\.name), ["voc_open", "voc_read_result"])
        XCTAssertNil(cursor)
        XCTAssertEqual(link.initialized.serverInfo.name, "voc-desktop")
        XCTAssertTrue(link.initialized.instructions?.contains("Pin report_id") == true)
        XCTAssertEqual(opener.calls, 0)
        for tool in tools { XCTAssertEqual(tool.inputSchema.objectValue?["additionalProperties"], false) }
        XCTAssertEqual(tools[0].annotations.readOnlyHint, false)
        XCTAssertEqual(tools[1].annotations.readOnlyHint, true)
        // Admission still succeeds: discovery did not create an active work.
        XCTAssertTrue(try store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")).shouldOpen)
        await link.close()
    }

    func testOpenReplayConflictAndSupersedeUseTheSameDurableDispatcher() async throws {
        let (directory, store) = try state()
        defer { try? FileManager.default.removeItem(at: directory) }
        let opener = Opener()
        let link = try await session(store, opener)
        let id = UUID().uuidString.lowercased()
        let arguments: [String: Value] = ["request_id": .string(id), "question": "Fuentes"]
        let first = try payload(await link.call("voc_open", arguments))
        let replay = try payload(await link.call("voc_open", arguments))
        XCTAssertEqual(first, replay)
        XCTAssertEqual(first["opening_status"], "opening")
        XCTAssertNil(first["layout_status"])
        XCTAssertEqual(try store.read(first["work_id"]!.stringValue!).presentation, .stdio)
        XCTAssertEqual(opener.calls, 1)
        let conflict = try payload(await link.call("voc_open", ["request_id": .string(id), "question": "Otra"]))
        XCTAssertEqual(conflict["error_code"], "request_conflict")
        let next = try payload(await link.call("voc_open", ["request_id": .string(UUID().uuidString.lowercased()), "question": "Fuentes"]))
        XCTAssertEqual(next["opening_status"], "opening")
        XCTAssertNotEqual(next["work_id"], first["work_id"])
        XCTAssertEqual(opener.calls, 2)
        XCTAssertFalse(try store.read(first["work_id"]!.stringValue!).active)
        let replayed = try payload(await link.call("voc_open", arguments))
        XCTAssertEqual(replayed["work_id"], first["work_id"])
        XCTAssertEqual(opener.calls, 2)
        await link.close()
    }

    func testInvalidArgumentsAreProtocolErrorsWithoutLaunching() async throws {
        let (directory, store) = try state()
        defer { try? FileManager.default.removeItem(at: directory) }
        let opener = Opener()
        let link = try await session(store, opener)
        let id: Value = .string(UUID().uuidString.lowercased())
        let cases: [(String, [String: Value])] = [
            ("unknown", [:]),
            ("voc_open", [:]),
            ("voc_open", ["request_id": id, "question": 3]),
            ("voc_open", ["request_id": id, "question": " "]),
            ("voc_open", ["request_id": id, "question": .string(String(repeating: "a", count: 1001))]),
            ("voc_open", ["request_id": "F0000000-0000-4000-8000-000000000001", "question": "Fuentes"]),
            ("voc_open", ["request_id": id, "question": "Fuentes", "server": "https://example.com"]),
            ("voc_open", ["request_id": id, "question": "Fuentes", "presentation": "http"]),
            ("voc_open", ["request_id": id, "question": "Fuentes", "window_id": 42]),
            ("voc_read_result", ["work_id": id, "report_id": "not-implemented"]),
            ("voc_read_result", ["work_id": "not-an-id"]),
        ]
        for (name, arguments) in cases {
            do { _ = try await link.call(name, arguments); XCTFail("Accepted invalid tool arguments") }
            catch let error as MCPError {
                guard case .invalidParams = error else { XCTFail("Unexpected MCP error: \(error)"); continue }
            }
        }
        XCTAssertEqual(opener.calls, 0)
        XCTAssertTrue(try store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")).shouldOpen)
        await link.close()
    }

    func testReadSurvivesNewSessionAndDoesNotClaimToReturnAnswersOrReports() async throws {
        let (directory, store) = try state()
        defer { try? FileManager.default.removeItem(at: directory) }
        let opener = Opener()
        let link = try await session(store, opener)
        let first = try payload(await link.call("voc_open", ["request_id": .string(UUID().uuidString.lowercased()), "question": "Private question"]))
        let id = try XCTUnwrap(first["work_id"]?.stringValue)
        _ = try store.claim(id, origin: "http://127.0.0.1:8091", owner: "gui")
        let receipt = HandoffReceipt(conversationID: UUID().uuidString.lowercased(), publicationID: UUID().uuidString.lowercased(), turnStatus: "running")
        _ = try store.received(id, owner: "gui", receipt: receipt)
        let profile = try ConnectionProfile(server: "http://127.0.0.1:8091", conversation: receipt.conversationID)
        _ = try store.opened(id, owner: "gui", url: profile.workspaceURL)
        await link.close()

        let reconnected = try await session(HandoffStore(directory: directory), opener)
        let read = try payload(await reconnected.call("voc_read_result", ["work_id": .string(id)]))
        XCTAssertEqual(read["opening_status"], "opened")
        XCTAssertEqual(read["conversation_id"], .string(receipt.conversationID))
        XCTAssertEqual(read["publication_id"], .string(receipt.publicationID))
        XCTAssertEqual(read["result_status"], "not_ready")
        XCTAssertEqual(read["result_reason"], "report_not_prepared")
        XCTAssertEqual(read["report"], .null)
        for key in ["question", "request_body", "server_origin", "claimed_by", "turn_status", "answer"] {
            XCTAssertNil(read[key])
        }
        XCTAssertEqual(opener.calls, 1)
        await reconnected.close()
    }

    func testFailureMissingAndAbandonedResultsDoNotRetryOpening() async throws {
        let (directory, store) = try state()
        defer { try? FileManager.default.removeItem(at: directory) }
        let opener = Opener()
        opener.fail = true
        let link = try await session(store, opener)
        let arguments: [String: Value] = ["request_id": .string(UUID().uuidString.lowercased()), "question": "Fuentes"]
        let failed = try payload(await link.call("voc_open", arguments))
        XCTAssertEqual(failed["error_code"], "launch_failed")
        XCTAssertEqual(failed["ok"], false)
        let id = try XCTUnwrap(failed["work_id"]?.stringValue)
        let read = try payload(await link.call("voc_read_result", ["work_id": .string(id)]))
        XCTAssertEqual(read["ok"], true)
        XCTAssertEqual(read["opening_status"], "failed")
        let missing = try payload(await link.call("voc_read_result", ["work_id": .string(UUID().uuidString.lowercased())]))
        XCTAssertEqual(missing["error_code"], "not_found")
        try store.abandon(id)
        let replay = try payload(await link.call("voc_open", arguments))
        XCTAssertEqual(replay["opening_status"], "abandoned")
        XCTAssertEqual(replay["ok"], false)
        XCTAssertEqual(opener.calls, 1)
        await link.close()
    }

    func testLateOpeningCallbacksCompleteOnlyOnce() async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let attempt = OpeningAttempt(continuation: continuation)
            attempt.finish(nil)
            attempt.finish(HandoffError("launch_failed"))
        }
        do {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                let attempt = OpeningAttempt(continuation: continuation)
                attempt.finish(HandoffError("launch_failed"))
                attempt.finish(nil)
            }
            XCTFail("Timeout failure was lost")
        } catch { XCTAssertEqual((error as? HandoffError)?.code, "launch_failed") }
    }
}

/// Exercise the built, bundled executable: protocol stdout, EOF and config output.
@MainActor
final class DesktopStdioTests: XCTestCase {
    func helper() throws -> URL {
        let products = Bundle(for: DesktopStdioTests.self).bundleURL.deletingLastPathComponent()
        let helper = products.appendingPathComponent("VOCDataState.app/Contents/Helpers/VOCDesktopBridge")
        XCTAssertTrue(FileManager.default.isExecutableFile(atPath: helper.path))
        return helper
    }

    func testBundledHelperNegotiatesListsAndExitsOnEOFWithoutOpeningWork() async throws {
        let process = Process()
        let input = Pipe(), output = Pipe(), errors = Pipe()
        process.executableURL = try helper()
        process.arguments = ["--stdio"]
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        let exited = expectation(description: "The helper exits on stdin EOF")
        process.terminationHandler = { _ in exited.fulfill() }
        try process.run()
        defer { if process.isRunning { process.terminate() } }

        let transport = StdioTransport(
            input: .init(rawValue: output.fileHandleForReading.fileDescriptor),
            output: .init(rawValue: input.fileHandleForWriting.fileDescriptor)
        )
        let client = Client(name: "voc-stdio-acceptance", version: "1")
        let initialized = try await client.connect(transport: transport)
        XCTAssertEqual(initialized.serverInfo.name, "voc-desktop")
        let (tools, _) = try await client.listTools()
        XCTAssertEqual(tools.map(\.name), ["voc_open", "voc_read_result"])
        let missing = try await client.callTool(name: "voc_read_result", arguments: ["work_id": .string(UUID().uuidString.lowercased())])
        XCTAssertEqual(missing.isError, true)
        do {
            _ = try await client.callTool(name: "voc_open", arguments: ["question": "invalid: no request ID"])
            XCTFail("Missing request_id was accepted")
        } catch let error as MCPError {
            guard case .invalidParams = error else { throw error }
        }
        // EOF must terminate only this helper. This test never requests a GUI launch.
        try input.fileHandleForWriting.close()
        await fulfillment(of: [exited], timeout: 5)
        await client.disconnect()
        XCTAssertFalse(process.isRunning)
        if !process.isRunning {
            XCTAssertEqual(process.terminationStatus, 0)
            try errors.fileHandleForWriting.close()
            XCTAssertEqual(try errors.fileHandleForReading.readToEnd() ?? Data(), Data())
        }
    }

    func testConfigUsesContainingBundleAndNeedsNoSecret() async throws {
        let process = Process()
        let output = Pipe(), errors = Pipe()
        let executable = try helper()
        process.executableURL = executable
        process.arguments = ["--claude-config"]
        process.standardOutput = output
        process.standardError = errors
        let exited = expectation(description: "Config generation completes")
        process.terminationHandler = { _ in exited.fulfill() }
        try process.run()
        defer { if process.isRunning { process.terminate() } }
        await fulfillment(of: [exited], timeout: 5)
        guard !process.isRunning else { return }
        XCTAssertEqual(process.terminationStatus, 0)
        try output.fileHandleForWriting.close()
        try errors.fileHandleForWriting.close()
        let data = try XCTUnwrap(output.fileHandleForReading.readToEnd())
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let servers = try XCTUnwrap(root["mcpServers"] as? [String: Any])
        let server = try XCTUnwrap(servers["voc-desktop"] as? [String: Any])
        XCTAssertEqual(Set(server.keys), ["command", "args"])
        XCTAssertEqual(server["command"] as? String, executable.resolvingSymlinksInPath().path)
        XCTAssertEqual(server["args"] as? [String], ["--stdio"])
        XCTAssertEqual(try errors.fileHandleForReading.readToEnd() ?? Data(), Data())
    }
}
