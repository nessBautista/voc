import XCTest
import MCP

actor SignInGatedTransport: HandoffTransport {
    private(set) var calls = 0
    let receipt = HandoffReceipt(conversationID: UUID().uuidString.lowercased(),
                                 publicationID: UUID().uuidString.lowercased(), turnStatus: "completed")
    func send(_ work: HandoffWork) async throws -> HandoffReceipt {
        calls += 1
        if calls == 1 { throw HandoffError("sign_in_required") }
        return receipt
    }
}

@MainActor
final class SignInHandoffTests: XCTestCase {
    func waitUntil(_ condition: @escaping () -> Bool) async throws {
        for _ in 0..<100 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTFail("Timed out waiting for the handoff transition")
    }

    func testRefusedDeliveryWaitsForSignInAndResumesExactlyOnce() async throws {
        let (directory, store, model, defaults) = try HandoffControllerTests().fixture()
        defer {
            defaults.removeObject(forKey: ConnectionModel.preferenceKey)
            try? FileManager.default.removeItem(at: directory)
        }
        let client = SignInGatedTransport()
        let controller = HandoffController(connection: model, store: store, client: client)
        var prompts = 0
        controller.onSignInRequired = { prompts += 1 }
        let work = try store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")).work
        controller.receive(work.link)
        try await waitUntil { controller.work?.state == .awaitingSignIn }
        XCTAssertEqual(prompts, 1)
        XCTAssertFalse(controller.isDelivering)
        XCTAssertTrue(controller.message.contains("inicio de sesión"))
        // Parked questions do not expire, replayed links do not resend, late failures do not apply.
        XCTAssertEqual(try store.read(work.workID, now: Date().timeIntervalSince1970 + 3600).state, .awaitingSignIn)
        controller.receive(work.link)
        try await Task.sleep(for: .milliseconds(60))
        let afterReplay = await client.calls
        XCTAssertEqual(afterReplay, 1)
        try store.fail(work.workID, owner: nil, code: "transport_failed")
        XCTAssertEqual(try store.read(work.workID).state, .awaitingSignIn)
        XCTAssertEqual(try store.latest()?.workID, work.workID)

        controller.resumeAfterSignIn()
        try await waitUntil { controller.work?.conversationID != nil }
        let afterResume = await client.calls
        XCTAssertEqual(afterResume, 2)
        XCTAssertEqual(controller.work?.requestID, work.requestID)
        controller.resumeAfterSignIn()
        try await Task.sleep(for: .milliseconds(60))
        let afterSecondResume = await client.calls
        XCTAssertEqual(afterSecondResume, 2)
    }

    func testPreparedReportsAreCachedForTheBridgeAndForgottenOnSignOut() async throws {
        let (directory, store, work) = try HandoffReportTests().fixture()
        defer { try? FileManager.default.removeItem(at: directory) }
        let data = Data("# VOC\nDatos café 🙂\n".utf8)
        let metadata = HandoffReportTests().report(work, data)
        let client = ReportTransportFixture([(metadata, data)])
        let controller = ReportController(store: store, client: client)
        await controller.prepare(work, origin: work.serverOrigin)
        XCTAssertEqual(try store.cachedReport(metadata.reportID), data)
        XCTAssertEqual(try store.read(work.workID).reports, [metadata])

        let pages = StoreReportPages(store: store)
        let assembled = try await pages.download(work, report: metadata)
        XCTAssertEqual(assembled, data)
        let opener = DesktopMCPTests.Opener()
        let link = try await DesktopMCPTests().session(store, opener, reports: StoreReportPages(store: store))
        let result = try await link.call("voc_read_result", ["work_id": .string(work.workID)])
        let payload = try DesktopMCPTests().payload(result)
        XCTAssertEqual(payload["result_status"], .string("ready"))
        XCTAssertEqual(payload["report"]?.objectValue?["report_id"], .string(metadata.reportID))
        await link.close()

        try store.clearReportCache()
        XCTAssertNil(try store.cachedReport(metadata.reportID))
        do { _ = try await pages.page(work, report: metadata, offset: 0); XCTFail("expected report_not_prepared") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "report_not_prepared") }
        XCTAssertThrowsError(try store.cacheReport(work.workID, report: metadata, bytes: Data("tampered".utf8)))
    }
}

final class BearerClientTests: XCTestCase {
    override func tearDown() {
        AuthStubProtocol.handler = nil
        AuthStubProtocol.requests = []
    }

    func testBearerIsSentAndAnUnauthorizedAnswerBecomesSignInRequired() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try HandoffStore(directory: directory)
        try store.configure(origin: "http://localhost:8091")
        let work = try store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")).work
        let conversation: [String: Any] = [
            "schema_version": "voc-conversation-v1",
            "conversation_id": UUID().uuidString.lowercased(),
            "publication_id": UUID().uuidString.lowercased(),
            "turns": [["request_id": work.requestID, "question": work.question, "status": "completed"]],
        ]
        AuthStubProtocol.handler = { request in
            guard request.value(forHTTPHeaderField: "Authorization") == "Bearer token-123" else {
                return (401, Data(#"{"error":{"code":"sign_in_required"}}"#.utf8))
            }
            return (202, try! JSONSerialization.data(withJSONObject: conversation))
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AuthStubProtocol.self]
        let signedIn = HandoffClient(bearer: { _ in "token-123" }, configuration: configuration)
        let receipt = try await signedIn.send(work)
        XCTAssertEqual(receipt.turnStatus, "completed")
        let anonymous = HandoffClient(configuration: configuration)
        do { _ = try await anonymous.send(work); XCTFail("expected sign_in_required") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "sign_in_required") }
        XCTAssertNil(AuthStubProtocol.requests.last?.value(forHTTPHeaderField: "Authorization"))
        let local = HandoffClient(bearer: { _ in nil }, configuration: configuration)
        do { _ = try await local.send(work); XCTFail("expected sign_in_required") }
        catch { XCTAssertEqual((error as? HandoffError)?.code, "sign_in_required") }
    }
}
