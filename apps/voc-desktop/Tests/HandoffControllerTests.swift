import XCTest

actor ScriptedTransport: HandoffTransport {
    private(set) var payloads: [String] = []
    private(set) var acceptedRequests = Set<String>()
    var failFirst = false
    let receipt = HandoffReceipt(
        conversationID: UUID().uuidString.lowercased(),
        publicationID: UUID().uuidString.lowercased(), turnStatus: "completed"
    )

    func loseFirstResponse() { failFirst = true }

    func send(_ work: HandoffWork) async throws -> HandoffReceipt {
        payloads.append(work.requestBody)
        acceptedRequests.insert(work.requestID)
        if failFirst && payloads.count == 1 { throw HandoffError("transport_failed") }
        return receipt
    }
}

@MainActor
final class HandoffControllerTests: XCTestCase {
    func fixture() throws -> (URL, HandoffStore, ConnectionModel, UserDefaults) {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let store = try HandoffStore(directory: directory)
        let defaults = UserDefaults(suiteName: "voc.handoff.tests.\(UUID().uuidString)")!
        let model = ConnectionModel(preferences: defaults, handoffStore: store)
        model.connect()
        return (directory, store, model, defaults)
    }

    func waitUntil(_ condition: @escaping () -> Bool) async throws {
        for _ in 0..<100 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("Timed out waiting for the handoff transition")
    }

    func testResponseLossRecoveryAndLinkReplayUseOneServerRequest() async throws {
        let (directory, store, model, defaults) = try fixture()
        defer {
            defaults.removeObject(forKey: ConnectionModel.preferenceKey)
            try? FileManager.default.removeItem(at: directory)
        }
        let client = ScriptedTransport()
        await client.loseFirstResponse()
        let controller = HandoffController(connection: model, store: store, client: client)
        let input = try HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")
        let work = try store.submit(input).work
        controller.receive(work.link)
        try await waitUntil { controller.work?.state == .failed }
        controller.recover()
        try await waitUntil { controller.work?.conversationID != nil }
        let command = try XCTUnwrap(model.command)
        XCTAssertEqual(command.profile.conversationID, controller.work?.conversationID)
        // An obsolete load cannot acknowledge the current handoff.
        model.loaded(commandID: UUID(), url: command.profile.workspaceURL)
        XCTAssertEqual(try store.read(work.workID).state, .opening)
        model.loaded(commandID: command.id, url: command.profile.workspaceURL)
        XCTAssertEqual(try store.read(work.workID).state, .opened)
        controller.receive(work.link)
        let bodies = await client.payloads
        let accepted = await client.acceptedRequests
        XCTAssertEqual(bodies, [work.requestBody, work.requestBody])
        XCTAssertEqual(accepted.count, 1)
        XCTAssertEqual(model.command?.id, command.id)
    }

    func testRestartRequiresExplicitRecoveryAndReceiptAvoidsAnotherPost() async throws {
        let (directory, store, model, defaults) = try fixture()
        defer {
            defaults.removeObject(forKey: ConnectionModel.preferenceKey)
            try? FileManager.default.removeItem(at: directory)
        }
        let client = ScriptedTransport()
        var controller: HandoffController? = HandoffController(connection: model, store: store, client: client)
        let work = try store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")).work
        controller!.receive(work.link)
        try await waitUntil { controller?.work?.conversationID != nil }
        // Simulate exit after persisting the receipt but before navigation finishes.
        controller = nil
        let nextModel = ConnectionModel(preferences: defaults, handoffStore: store)
        let restored = HandoffController(connection: nextModel, store: store, client: client)
        XCTAssertNil(nextModel.command)
        XCTAssertFalse(restored.isDelivering)
        restored.recover()
        try await waitUntil { nextModel.command?.profile.conversationID != nil }
        let payloads = await client.payloads
        XCTAssertEqual(payloads.count, 1)
        let command = try XCTUnwrap(nextModel.command)
        nextModel.loaded(commandID: command.id, url: command.profile.workspaceURL)
        XCTAssertEqual(try store.read(work.workID).state, .opened)
        restored.abandon()
        XCTAssertEqual(try store.read(work.workID).state, .abandoned)
        XCTAssertNotNil(try store.read(work.workID).conversationID)
    }

    func testNavigationFailureCanRecoverAndWrongOriginDoesNotSend() async throws {
        let (directory, store, model, defaults) = try fixture()
        defer {
            defaults.removeObject(forKey: ConnectionModel.preferenceKey)
            try? FileManager.default.removeItem(at: directory)
        }
        let client = ScriptedTransport()
        let controller = HandoffController(connection: model, store: store, client: client)
        let work = try store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")).work
        model.server = "http://127.0.0.1:8099"
        model.connect()
        controller.receive(work.link)
        XCTAssertNil(try store.read(work.workID).claimedBy)
        let before = await client.payloads
        XCTAssertTrue(before.isEmpty)
        model.server = work.serverOrigin
        model.connect()
        controller.recover()
        try await waitUntil { controller.work?.conversationID != nil }
        let failed = try XCTUnwrap(model.command)
        model.failed("Offline", commandID: failed.id)
        XCTAssertEqual(try store.read(work.workID).errorCode, "navigation_failed")
        controller.recover()
        try await waitUntil { model.command?.id != failed.id }
        let next = try XCTUnwrap(model.command)
        model.loaded(commandID: next.id, url: next.profile.workspaceURL)
        let after = await client.payloads
        XCTAssertEqual(after.count, 1)
        XCTAssertEqual(try store.read(work.workID).state, .opened)
    }
}
