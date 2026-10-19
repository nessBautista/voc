import Foundation
import MCP

/// Transport adapters share these tools; the native handoff remains the only dispatcher.
@MainActor
enum DesktopMCP {
    static let version = "0.3.0"
    static let instructions = """
    Use VOC Desktop when the user asks to open VOC and continue exploring its published data there.
    voc_open opens a native Mac window and hands the initial question to the configured VOC server.
    Keep the returned work_id. Reuse the same request_id and question only to retry that handoff.
    Wait for the user to return before reading a result; do not repeatedly poll for a report.
    This version returns opening status only. Markdown reports and automatic window layout are not yet available.
    The current VOC server answers questions about data sources, record counts, dates, coverage and preparation metadata.
    """

    static let tools: [Tool] = [
        Tool(
            name: "voc_open",
            title: "Open VOC Desktop",
            description: """
            Open VOC Desktop on this Mac with one initial question and let the user continue in its chat.
            This creates a local handoff and the GUI sends the question to the user's configured VOC server,
            which may use its configured model provider. Call when the user asks to work in VOC.
            Supply a new canonical lowercase UUID request_id for a new handoff; retain it for retries.
            A retry with the same question returns the same work without sending or focusing again.
            Another active work returns busy; the user can finish or abandon it inside VOC.
            Retain work_id and conversation_id. opening means navigation is pending; opened confirms navigation,
            not answer completion. This version does not rearrange windows or generate reports.
            """,
            inputSchema: .object([
                "type": "object",
                "properties": .object([
                    "request_id": uuidSchema,
                    "question": .object(["type": "string", "minLength": 1, "maxLength": 1000]),
                ]),
                "required": ["request_id", "question"],
                "additionalProperties": false,
            ]),
            annotations: .init(readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true)
        ),
        Tool(
            name: "voc_read_result",
            title: "Read VOC Desktop work status",
            description: """
            Read the saved opening status for a work_id returned by voc_open. This never opens or focuses
            a window, sends a chat question, or calls the VOC model. Reports are not implemented in this
            version: result_status is always not_ready and report is null, even after the user finishes
            chatting. report_unavailable is a capability limitation, not a request to poll until ready.
            Ask the user to continue in VOC; do not claim to have read its answers or generated an export.
            """,
            inputSchema: .object([
                "type": "object",
                "properties": .object(["work_id": uuidSchema]),
                "required": ["work_id"],
                "additionalProperties": false,
            ]),
            annotations: .init(readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false)
        ),
    ]

    private static let uuidSchema: Value = .object([
        "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36,
        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    ])

    static func make(store: HandoffStore, opener: any HandoffOpening) async -> Server {
        let server = Server(
            name: "voc-desktop", version: version, title: "VOC Desktop",
            instructions: instructions,
            capabilities: .init(tools: .init(listChanged: false)),
            configuration: .strict
        )
        let launcher = HandoffLauncher(store: store, opener: opener)
        await server.withMethodHandler(ListTools.self) { parameters in
            guard parameters.cursor == nil else { throw MCPError.invalidParams("This tool list has no pagination.") }
            return await ListTools.Result(tools: tools)
        }
        await server.withMethodHandler(CallTool.self) { parameters in
            try await call(parameters, store: store, launcher: launcher)
        }
        return server
    }

    static func serveStdio(store: HandoffStore, opener: any HandoffOpening) async throws {
        // The SDK owns framing, initialization and EOF. Its default transport logger is silent.
        let server = await make(store: store, opener: opener)
        let transport = StdioTransport()
        try await server.start(transport: transport)
        await server.waitUntilCompleted()
        await server.stop()
    }

    private static func call(_ parameters: CallTool.Parameters, store: HandoffStore,
                             launcher: HandoffLauncher) async throws -> CallTool.Result {
        let arguments = parameters.arguments ?? [:]
        switch parameters.name {
        case "voc_open":
            try require(arguments, keys: ["request_id", "question"])
            let input: HandoffInput
            do {
                input = try HandoffInput(
                    requestID: arguments["request_id"]!.stringValue!,
                    question: arguments["question"]!.stringValue!
                )
            } catch let error as HandoffError {
                throw MCPError.invalidParams(error.localizedDescription)
            }
            do {
                let work = try await launcher.submit(input)
                return try result(work, openingRequest: true)
            } catch { return try failure(error) }
        case "voc_read_result":
            try require(arguments, keys: ["work_id"])
            let identity: String
            do { identity = try HandoffInput.identity(arguments["work_id"]!.stringValue!) }
            catch { throw MCPError.invalidParams("Use a canonical lowercase UUID for work_id.") }
            do { return try result(store.read(identity), openingRequest: false) }
            catch { return try failure(error) }
        default:
            throw MCPError.invalidParams("Unknown VOC Desktop tool.")
        }
    }

    private static func require(_ arguments: [String: Value], keys: Set<String>) throws {
        guard Set(arguments.keys) == keys,
              arguments.values.allSatisfy({ $0.stringValue != nil }) else {
            throw MCPError.invalidParams("Supply exactly the documented string arguments.")
        }
    }

    private static func result(_ work: HandoffWork, openingRequest: Bool) throws -> CallTool.Result {
        let unsuccessful = openingRequest && [.failed, .expired, .abandoned].contains(work.state)
        let message: String
        if unsuccessful {
            message = HandoffError(work.errorCode ?? work.state.rawValue).localizedDescription
        } else if work.state == .opening {
            message = "Opening is pending. Keep work_id; the user continues in VOC. Reports are not available in this version."
        } else {
            message = "Saved opening status. The user continues in VOC. Reports are not available in this version."
        }
        // Return only the public contract, not the stored question, HTTP payload, origin or GUI owner.
        return try response(.object([
            "schema_version": "voc-desktop-mcp-v1",
            "ok": .bool(!unsuccessful),
            "work_id": .string(work.workID),
            "request_id": .string(work.requestID),
            "opening_status": .string(work.state.rawValue),
            "active": .bool(work.active),
            "conversation_id": work.conversationID.map(Value.string) ?? .null,
            "publication_id": work.publicationID.map(Value.string) ?? .null,
            "result_status": "not_ready",
            "result_reason": "report_unavailable",
            "report": .null,
            "capabilities": .object(["markdown_export": false, "window_layout": false]),
            "error_code": work.errorCode.map(Value.string) ?? .null,
            "message": .string(message),
        ]), isError: unsuccessful)
    }

    private static func failure(_ error: Error) throws -> CallTool.Result {
        let failure = error as? HandoffError ?? HandoffError("storage_unavailable")
        return try response(.object([
            "schema_version": "voc-desktop-mcp-v1", "ok": false,
            "error_code": .string(failure.code),
            "message": .string(failure.localizedDescription),
        ]), isError: true)
    }

    private static func response(_ payload: Value, isError: Bool) throws -> CallTool.Result {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let text = String(decoding: try encoder.encode(payload), as: UTF8.self)
        return try CallTool.Result(
            content: [.text(text: text, annotations: nil, _meta: nil)],
            structuredContent: payload, isError: isError
        )
    }
}
