import XCTest

final class HandoffTests: XCTestCase {
    var directory: URL!
    var store: HandoffStore!
    let origin = "http://127.0.0.1:8091"

    override func setUpWithError() throws {
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        store = try HandoffStore(directory: directory)
        try store.configure(origin: origin)
    }

    override func tearDownWithError() throws {
        store = nil
        try FileManager.default.removeItem(at: directory)
    }

    func input(_ question: String = "¿Cuántos registros tenemos de Google Play?", id: String = UUID().uuidString.lowercased()) throws -> HandoffInput {
        try HandoffInput(requestID: id, question: question)
    }

    func assertCode(_ expected: String, _ body: () throws -> Void, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try body(), file: file, line: line) {
            XCTAssertEqual(($0 as? HandoffError)?.code, expected, file: file, line: line)
        }
    }

    func testInputMatchesAskNormalizationAndCodePointLimit() throws {
        let request = try input(" \u{001c} ¿Hay datos? \n")
        XCTAssertEqual(request.question, "¿Hay datos?")
        let body = try JSONSerialization.jsonObject(with: Data(request.body.utf8)) as! [String: Any]
        XCTAssertTrue(body["conversation_id"] is NSNull)
        XCTAssertEqual(Set(body.keys), ["request_id", "question", "conversation_id"])
        for question in ["", " \n", String(repeating: "a", count: 1001), String(repeating: "e\u{0301}", count: 501)] {
            assertCode("invalid_question") { _ = try self.input(question) }
        }
        XCTAssertNoThrow(try input(String(repeating: "😀", count: 1000)))
        assertCode("invalid_id") { _ = try self.input(id: "F0000000-0000-4000-8000-000000000001") }
    }

    func testOneRequestOneWorkAndPinnedOriginAcrossReconfiguration() throws {
        let request = try input()
        let first = try store.submit(request)
        XCTAssertTrue(first.shouldOpen)
        try store.configure(origin: "http://127.0.0.1:8099")
        let replay = try store.submit(input(" \(request.question) ", id: request.requestID))
        XCTAssertFalse(replay.shouldOpen)
        XCTAssertEqual(replay.work, first.work)
        assertCode("request_conflict") { _ = try self.store.submit(self.input("Other?", id: request.requestID)) }
        assertCode("busy") { _ = try self.store.submit(self.input()) }
        assertCode("origin_changed") { _ = try self.store.claim(first.work.workID, origin: self.origin, owner: "gui") }
        XCTAssertNil(try store.read(first.work.workID).claimedBy)
    }

    func testIndependentConnectionsRaceToRegisterAndClaimOnlyOnce() throws {
        let second = try HandoffStore(directory: directory)
        let request = try input()
        let queue = DispatchQueue(label: "results")
        var works: [HandoffWork] = []
        var openings = 0
        var errors: [Error] = []
        DispatchQueue.concurrentPerform(iterations: 20) { index in
            do {
                let result = try (index.isMultiple(of: 2) ? store! : second).submit(request)
                queue.sync { works.append(result.work); if result.shouldOpen { openings += 1 } }
            } catch { queue.sync { errors.append(error) } }
        }
        XCTAssertTrue(errors.isEmpty)
        XCTAssertEqual(openings, 1)
        XCTAssertEqual(Set(works.map(\.workID)).count, 1)
        let id = try XCTUnwrap(works.first?.workID)
        var claims = 0
        DispatchQueue.concurrentPerform(iterations: 20) { index in
            do {
                let result = try (index.isMultiple(of: 2) ? store! : second).claim(id, origin: origin, owner: "gui-\(index)")
                if result != nil { queue.sync { claims += 1 } }
            } catch { queue.sync { errors.append(error) } }
        }
        XCTAssertEqual(claims, 1)
        XCTAssertTrue(errors.isEmpty)
    }

    func testUnclaimedExpiryIsPersistentAndDoesNotDispatchOnReplay() throws {
        let request = try input()
        let work = try store.submit(request, now: 100).work
        XCTAssertNil(try store.claim(work.workID, origin: origin, owner: "gui", now: 160))
        XCTAssertEqual(try store.read(work.workID, now: 161).state, .expired)
        XCTAssertFalse(try store.submit(request, now: 200).shouldOpen)
        XCTAssertTrue(try store.submit(input(), now: 201).shouldOpen)
    }

    func testAcceptedTurnDoesNotExpireAndLostResponseReusesExactPayload() throws {
        let original = try store.submit(input(), now: 100).work
        _ = try store.claim(original.workID, origin: origin, owner: "old", now: 101)
        XCTAssertEqual(try store.read(original.workID, now: 999).state, .opening)
        // A restarted GUI recovers explicitly under its process lease.
        let restored = try HandoffStore(directory: directory)
        let recovered = try XCTUnwrap(restored.claim(original.workID, origin: origin, owner: "new", recover: true, now: 1000))
        XCTAssertEqual(recovered.requestBody, original.requestBody)
        let receipt = HandoffReceipt(conversationID: UUID().uuidString.lowercased(), publicationID: UUID().uuidString.lowercased(), turnStatus: "interrupted")
        let received = try restored.received(original.workID, owner: "new", receipt: receipt)
        XCTAssertEqual(received.requestBody, original.requestBody)
        XCTAssertEqual(received.turnStatus, "interrupted")
        assertCode("inactive") { _ = try self.store.received(original.workID, owner: "old", receipt: receipt) }
        let profile = try ConnectionProfile(server: origin, conversation: receipt.conversationID)
        assertCode("navigation_failed") { _ = try restored.opened(original.workID, owner: "new", url: URL(string: self.origin)!) }
        XCTAssertEqual(try restored.opened(original.workID, owner: "new", url: profile.workspaceURL).state, .opened)
        XCTAssertNil(try restored.claim(original.workID, origin: origin, owner: "third"))
    }

    func testFailureAndAbandonReleaseAdmissionWithoutLettingLateCallbacksReviveWork() throws {
        let work = try store.submit(input()).work
        _ = try store.claim(work.workID, origin: origin, owner: "gui")
        try store.fail(work.workID, owner: nil, code: "launch_failed")
        XCTAssertEqual(try store.read(work.workID).state, .opening)
        try store.fail(work.workID, owner: "gui", code: "transport_failed")
        let next = try store.submit(input()).work
        assertCode("busy") { _ = try self.store.claim(work.workID, origin: self.origin, owner: "gui", recover: true) }
        try store.abandon(next.workID)
        _ = try store.claim(work.workID, origin: origin, owner: "gui", recover: true)
        try store.abandon(work.workID)
        let receipt = HandoffReceipt(conversationID: UUID().uuidString.lowercased(), publicationID: UUID().uuidString.lowercased(), turnStatus: "running")
        assertCode("inactive") { _ = try self.store.received(work.workID, owner: "gui", receipt: receipt) }
        XCTAssertNil(try store.claim(work.workID, origin: origin, owner: "gui", recover: true))
        XCTAssertFalse(try store.submit(input(work.question, id: work.requestID)).shouldOpen)
    }

    func testLinksCannotCreateWorkOrSmuggleParameters() throws {
        let work = try store.submit(input()).work
        XCTAssertEqual(try HandoffWork.identity(from: work.link), work.workID)
        for text in ["voc://handoff/\(work.workID)", "voc-data-state://handoff/\(work.workID)?question=hello", "voc-data-state://handoff/\(work.workID)#x", "voc-data-state://user@handoff/\(work.workID)", "voc-data-state://handoff:80/\(work.workID)", "voc-data-state://handoff/../\(work.workID)", "voc-data-state://handoff/%61\(work.workID.dropFirst())"] {
            XCTAssertThrowsError(try HandoffWork.identity(from: XCTUnwrap(URL(string: text))))
        }
        assertCode("not_found") { _ = try self.store.claim(UUID().uuidString.lowercased(), origin: self.origin, owner: "gui") }
    }

    func testOnlyOneGUILeaseAndPrivateStatePermissions() throws {
        var lease: DesktopLease? = try DesktopLease(directory: directory)
        XCTAssertNotNil(lease)
        assertCode("desktop_busy") { _ = try DesktopLease(directory: self.directory) }
        lease = nil
        XCTAssertNoThrow(try DesktopLease(directory: directory))
        for (url, mode) in [(directory!, 0o700), (directory.appendingPathComponent("handoffs.sqlite3"), 0o600)] {
            let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
            XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, mode)
        }
    }

    func testReceiptRequiresTheRequestedTurnAndCanonicalIdentities() throws {
        let work = try store.submit(input()).work
        var payload: [String: Any] = [
            "schema_version": "voc-conversation-v1", "conversation_id": UUID().uuidString.lowercased(),
            "publication_id": UUID().uuidString.lowercased(),
            "turns": [["request_id": work.requestID, "question": work.question, "status": "failed"]],
        ]
        func data() throws -> Data { try JSONSerialization.data(withJSONObject: payload) }
        XCTAssertEqual(try HandoffReceipt.decode(data(), for: work).turnStatus, "failed")
        payload["turns"] = [["request_id": work.requestID, "question": "changed", "status": "completed"]]
        XCTAssertThrowsError(try HandoffReceipt.decode(data(), for: work))
        payload["turns"] = []
        XCTAssertThrowsError(try HandoffReceipt.decode(data(), for: work))
    }
}

@MainActor
final class HandoffLauncherTests: XCTestCase {
    final class Opener: HandoffOpening {
        var calls = 0
        var fail = false
        func open(_ work: HandoffWork) async throws {
            calls += 1
            if fail { throw HandoffError("launch_failed") }
        }
    }

    func testLauncherRetryAndReadNeverOpenAgainEvenAfterLaunchFailure() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try HandoffStore(directory: directory)
        try store.configure(origin: "http://127.0.0.1:8091")
        let opener = Opener()
        opener.fail = true
        let launcher = HandoffLauncher(store: store, opener: opener)
        let input = try HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")
        let first = try await launcher.submit(input)
        XCTAssertEqual(first.state, .failed)
        let retry = try await launcher.submit(input)
        XCTAssertEqual(retry.workID, first.workID)
        XCTAssertEqual(opener.calls, 1)
        _ = try store.read(first.workID)
        XCTAssertEqual(opener.calls, 1)
    }
}
