import XCTest
import MCP

actor ReportTransportFixture: HandoffReportTransport {
    let content: [String: (HandoffReport, Data)]
    var selected: String
    var failFirst = false
    private(set) var preparations: [String] = []
    private(set) var pages = 0

    init(_ reports: [(HandoffReport, Data)]) {
        content = Dictionary(uniqueKeysWithValues: reports.map { ($0.0.reportID, $0) })
        selected = reports[0].0.reportID
    }
    func loseResponse() { failFirst = true }
    func prepare(_ work: HandoffWork, requestID: String) async throws -> HandoffReport {
        preparations.append(requestID)
        if failFirst && preparations.count == 1 { throw HandoffError("report_transport_failed") }
        return content[selected]!.0
    }
    func page(_ work: HandoffWork, report: HandoffReport, offset: Int) async throws -> HandoffReportPage {
        pages += 1
        let stored = content[report.reportID]!
        guard (0...stored.1.count).contains(offset) else { throw HandoffError("invalid_report_offset") }
        let end = min(offset + HandoffReport.chunkBytes, stored.1.count)
        return HandoffReportPage(report: stored.0, chunk: .init(
            encoding: "base64", data: stored.1.subdata(in: offset..<end).base64EncodedString(),
            offset: offset, nextOffset: end, eof: end == stored.1.count
        ))
    }
}

@MainActor
final class HandoffReportTests: XCTestCase {
    func fixture() throws -> (URL, HandoffStore, HandoffWork) {
        let (directory, store) = try DesktopMCPTests().state()
        let work = try store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Fuentes")).work
        _ = try store.claim(work.workID, origin: work.serverOrigin, owner: "gui")
        let receipt = HandoffReceipt(conversationID: UUID().uuidString.lowercased(), publicationID: UUID().uuidString.lowercased(), turnStatus: "completed")
        _ = try store.received(work.workID, owner: "gui", receipt: receipt)
        let opened = try store.opened(work.workID, owner: "gui", url: ConnectionProfile(server: work.serverOrigin, conversation: receipt.conversationID).workspaceURL)
        return (directory, store, opened)
    }

    func report(_ work: HandoffWork, _ data: Data, turns: Int = 2) -> HandoffReport {
        HandoffReport(schemaVersion: "voc-report-v1", reportID: UUID().uuidString.lowercased(),
            conversationID: work.conversationID!, publicationID: work.publicationID!,
            createdAt: "2026-10-19T02:00:00.123456Z", turnCount: turns,
            snapshotSHA256: String(repeating: "a", count: 64), byteLength: data.count,
            sha256: HandoffReport.digest(data), mediaType: "text/markdown; charset=utf-8")
    }

    func attach(_ store: HandoffStore, _ work: HandoffWork, _ report: HandoffReport) throws {
        let pending = try store.reserveReport(work.workID, origin: work.serverOrigin)
        _ = try store.preparedReport(work.workID, requestID: pending.requestID, report: report)
    }

    func testPreparationLossRetainsRequestAndReadyKeepsTheConversation() async throws {
        let (directory, store, work) = try fixture()
        defer { try? FileManager.default.removeItem(at: directory) }
        let data = Data("# VOC\nDatos café 🙂\n".utf8)
        let metadata = report(work, data)
        let client = ReportTransportFixture([(metadata, data)])
        await client.loseResponse()
        let controller = ReportController(store: store, client: client)
        await controller.prepare(work, origin: work.serverOrigin)
        XCTAssertTrue(try store.read(work.workID).active)
        XCTAssertNotNil(try store.read(work.workID).reportRequestID)
        XCTAssertNil(try store.read(work.workID).reports)
        await controller.prepare(work, origin: work.serverOrigin)
        let requests = await client.preparations
        XCTAssertEqual(requests.count, 2)
        XCTAssertEqual(requests[0], requests[1])
        let saved = try HandoffStore(directory: directory).read(work.workID)
        XCTAssertTrue(saved.active)
        XCTAssertNil(saved.reportRequestID)
        XCTAssertEqual(saved.reports, [metadata])
        XCTAssertEqual(saved.state, .opened)
        // A ready handoff stays done, including on an explicit old recovery/link.
        XCTAssertNil(try store.claim(work.workID, origin: work.serverOrigin, owner: "other", recover: true))
        XCTAssertTrue(try store.submit(HandoffInput(requestID: UUID().uuidString.lowercased(), question: "Otra")).shouldOpen)
        let restored = try store.report(work.workID, reportID: metadata.reportID)
        XCTAssertEqual(restored.report, metadata)
    }

    func testWrongOriginWrongReportAndLateCompletionCannotReleaseWork() throws {
        let (directory, store, work) = try fixture()
        defer { try? FileManager.default.removeItem(at: directory) }
        XCTAssertThrowsError(try store.reserveReport(work.workID, origin: "http://127.0.0.1:8099"))
        let pending = try store.reserveReport(work.workID, origin: work.serverOrigin)
        var foreign = work
        foreign.conversationID = UUID().uuidString.lowercased()
        XCTAssertThrowsError(try store.preparedReport(work.workID, requestID: pending.requestID, report: report(foreign, Data("bad".utf8))))
        XCTAssertTrue(try store.read(work.workID).active)
        try store.abandon(work.workID)
        XCTAssertThrowsError(try store.preparedReport(work.workID, requestID: pending.requestID, report: report(work, Data("late".utf8))))
        XCTAssertNil(try store.read(work.workID).reports)
    }

    func testUnicodeIsAssembledBeforeDecodingAndDigestIsVerified() async throws {
        let (directory, _, work) = try fixture()
        defer { try? FileManager.default.removeItem(at: directory) }
        let bytes = Data((String(repeating: "x", count: HandoffReport.chunkBytes - 1) + "🙂 café 漢字\n" + String(repeating: "final ", count: 7000)).utf8)
        let metadata = report(work, bytes)
        let client = ReportTransportFixture([(metadata, bytes)])
        let first = try await client.page(work, report: metadata, offset: 0)
        XCTAssertNil(String(data: try first.bytes(expected: metadata, offset: 0), encoding: .utf8))
        let result = try await client.download(work, report: metadata)
        XCTAssertEqual(result, bytes)
        XCTAssertEqual(HandoffReport.digest(result), metadata.sha256)
    }

    func testPageCannotChangeIdentityPositionLengthOrFinalDigest() throws {
        let (directory, _, work) = try fixture()
        defer { try? FileManager.default.removeItem(at: directory) }
        let bytes = Data("abc".utf8)
        let metadata = report(work, bytes)
        let cases: [HandoffReportPage] = [
            .init(report: report(work, bytes), chunk: .init(encoding: "base64", data: bytes.base64EncodedString(), offset: 0, nextOffset: 3, eof: true)),
            .init(report: metadata, chunk: .init(encoding: "utf8", data: "abc", offset: 0, nextOffset: 3, eof: true)),
            .init(report: metadata, chunk: .init(encoding: "base64", data: Data("xyz".utf8).base64EncodedString(), offset: 0, nextOffset: 3, eof: true)),
            .init(report: metadata, chunk: .init(encoding: "base64", data: bytes.base64EncodedString(), offset: 1, nextOffset: 3, eof: true)),
            .init(report: metadata, chunk: .init(encoding: "base64", data: bytes.base64EncodedString(), offset: 0, nextOffset: 3, eof: false)),
            .init(report: metadata, chunk: .init(encoding: "base64", data: "", offset: 0, nextOffset: 0, eof: false)),
        ]
        for page in cases { XCTAssertThrowsError(try page.bytes(expected: metadata, offset: 0)) }
    }

    func testMCPReadsEveryByteAndPinsOldReportAfterAnotherExport() async throws {
        let (directory, store, work) = try fixture()
        defer { try? FileManager.default.removeItem(at: directory) }
        let firstBytes = Data((String(repeating: "x", count: HandoffReport.chunkBytes - 1) + String(repeating: "🙂 café 漢字\n", count: 7000)).utf8)
        let secondBytes = Data("# Otro informe\n".utf8)
        let firstReport = report(work, firstBytes)
        let secondReport = report(work, secondBytes, turns: 3)
        let client = ReportTransportFixture([(firstReport, firstBytes), (secondReport, secondBytes)])
        let test = DesktopMCPTests(), opener = DesktopMCPTests.Opener()
        let link = try await test.session(store, opener, reports: client)
        let notReady = try test.payload(await link.call("voc_read_result", ["work_id": .string(work.workID)]))
        XCTAssertEqual(notReady["result_status"], "not_ready")
        let noPages = await client.pages
        XCTAssertEqual(noPages, 0)
        try attach(store, work, firstReport)
        let initial = try test.payload(await link.call("voc_read_result", ["work_id": .string(work.workID)]))
        XCTAssertEqual(initial["result_status"], "ready")
        XCTAssertEqual(initial["report"]?.objectValue?["report_id"], .string(firstReport.reportID))
        try attach(store, work, secondReport)
        var assembled = Data()
        var current = initial
        while true {
            let chunk = try JSONDecoder().decode(HandoffReportPage.Chunk.self, from: JSONEncoder().encode(current["chunk"]!))
            XCTAssertEqual(current["report"]?.objectValue?["report_id"], .string(firstReport.reportID))
            XCTAssertEqual(chunk.offset, assembled.count)
            assembled.append(try XCTUnwrap(Data(base64Encoded: chunk.data)))
            if chunk.eof { break }
            current = try test.payload(await link.call("voc_read_result", ["work_id": .string(work.workID), "report_id": .string(firstReport.reportID), "offset": .int(chunk.nextOffset)]))
        }
        XCTAssertEqual(assembled, firstBytes)
        XCTAssertEqual(HandoffReport.digest(assembled), firstReport.sha256)
        let latest = try test.payload(await link.call("voc_read_result", ["work_id": .string(work.workID)]))
        XCTAssertEqual(latest["report"]?.objectValue?["report_id"], .string(secondReport.reportID))
        XCTAssertEqual(opener.calls, 0)
        let prepareCalls = await client.preparations
        XCTAssertTrue(prepareCalls.isEmpty)
        let foreign = try test.payload(await link.call("voc_read_result", ["work_id": .string(work.workID), "report_id": .string(UUID().uuidString.lowercased())]))
        XCTAssertEqual(foreign["error_code"], "report_not_associated")
        for arguments: [String: Value] in [
            ["work_id": .string(work.workID), "offset": 1],
            ["work_id": .string(work.workID), "report_id": .string(firstReport.reportID), "offset": -1],
            ["work_id": .string(work.workID), "report_id": .string(firstReport.reportID), "offset": true],
            ["work_id": .string(work.workID), "report_id": .string(firstReport.reportID), "offset": "1"],
            ["work_id": .string(work.workID), "report_id": .string(firstReport.reportID), "limit": 20],
        ] {
            do { _ = try await link.call("voc_read_result", arguments); XCTFail("Invalid report arguments accepted") }
            catch let error as MCPError { guard case .invalidParams = error else { throw error } }
        }
        await link.close()
        let reopened = try await test.session(HandoffStore(directory: directory), opener, reports: client)
        let old = try test.payload(await reopened.call("voc_read_result", ["work_id": .string(work.workID), "report_id": .string(firstReport.reportID)]))
        XCTAssertEqual(old["report"]?.objectValue?["report_id"], .string(firstReport.reportID))
        await reopened.close()
    }
}
