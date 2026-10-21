import Foundation
import CryptoKit

/// Immutable metadata travels with every page; a prepared work retains all older reports.
struct HandoffReport: Codable, Equatable, Sendable {
    static let maximumBytes = 8 * 1024 * 1024
    static let chunkBytes = 32 * 1024
    let schemaVersion: String
    let reportID: String
    let conversationID: String
    let publicationID: String
    let createdAt: String
    let turnCount: Int
    let snapshotSHA256: String
    let byteLength: Int
    let sha256: String
    let mediaType: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version", reportID = "report_id"
        case conversationID = "conversation_id", publicationID = "publication_id"
        case createdAt = "created_at", turnCount = "turn_count"
        case snapshotSHA256 = "snapshot_sha256", byteLength = "byte_length"
        case sha256, mediaType = "media_type"
    }

    func validate(for work: HandoffWork) throws {
        _ = try HandoffInput.identity(reportID)
        _ = try HandoffInput.identity(conversationID)
        _ = try HandoffInput.identity(publicationID)
        let date = ISO8601DateFormatter()
        date.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard schemaVersion == "voc-report-v1", mediaType == "text/markdown; charset=utf-8",
              conversationID == work.conversationID, publicationID == work.publicationID,
              (1...100).contains(turnCount), (1...Self.maximumBytes).contains(byteLength),
              Self.isDigest(sha256), Self.isDigest(snapshotSHA256), date.date(from: createdAt) != nil
        else { throw HandoffError("invalid_report") }
    }

    static func digest(_ bytes: Data) -> String {
        SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }

    private static func isDigest(_ value: String) -> Bool {
        value.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}

struct HandoffReportPage: Codable, Equatable, Sendable {
    struct Chunk: Codable, Equatable, Sendable {
        let encoding: String
        let data: String
        let offset: Int
        let nextOffset: Int
        let eof: Bool
        enum CodingKeys: String, CodingKey {
            case encoding, data, offset, eof, nextOffset = "next_offset"
        }
    }
    let report: HandoffReport
    let chunk: Chunk

    func bytes(expected: HandoffReport, offset: Int) throws -> Data {
        guard report == expected, chunk.encoding == "base64", chunk.offset == offset,
              (0...report.byteLength).contains(offset),
              let bytes = Data(base64Encoded: chunk.data), bytes.base64EncodedString() == chunk.data,
              bytes.count == min(HandoffReport.chunkBytes, report.byteLength - offset),
              chunk.nextOffset == offset + bytes.count,
              chunk.eof == (chunk.nextOffset == report.byteLength) else {
            throw HandoffError("invalid_report")
        }
        if offset == 0 && chunk.eof && HandoffReport.digest(bytes) != report.sha256 {
            throw HandoffError("invalid_report")
        }
        return bytes
    }
}

protocol HandoffReportTransport {
    func prepare(_ work: HandoffWork, requestID: String) async throws -> HandoffReport
    func page(_ work: HandoffWork, report: HandoffReport, offset: Int) async throws -> HandoffReportPage
}

extension HandoffReportTransport {
    /// Decode UTF-8 only after assembling bytes: a page can divide a Unicode character.
    func download(_ work: HandoffWork, report: HandoffReport) async throws -> Data {
        try report.validate(for: work)
        let deadline = Date().addingTimeInterval(60)
        var data = Data()
        while data.count < report.byteLength {
            try Task.checkCancellation()
            guard Date() < deadline else { throw HandoffError("report_transport_failed") }
            let result = try await page(work, report: report, offset: data.count)
            data.append(try result.bytes(expected: report, offset: data.count))
        }
        guard HandoffReport.digest(data) == report.sha256,
              String(data: data, encoding: .utf8) != nil else { throw HandoffError("invalid_report") }
        return data
    }
}

/// The bridge serves Claude from the bytes the person prepared in VOC; it never calls the server.
struct StoreReportPages: HandoffReportTransport {
    let store: HandoffStore

    func prepare(_ work: HandoffWork, requestID: String) async throws -> HandoffReport {
        throw HandoffError("report_not_prepared")
    }

    func page(_ work: HandoffWork, report: HandoffReport, offset: Int) async throws -> HandoffReportPage {
        try report.validate(for: work)
        guard let bytes = try store.cachedReport(report.reportID), bytes.count == report.byteLength,
              HandoffReport.digest(bytes) == report.sha256 else { throw HandoffError("report_not_prepared") }
        guard (0...bytes.count).contains(offset) else { throw HandoffError("invalid_report_offset") }
        let end = min(offset + HandoffReport.chunkBytes, bytes.count)
        return HandoffReportPage(report: report, chunk: .init(
            encoding: "base64", data: bytes.subdata(in: offset..<end).base64EncodedString(),
            offset: offset, nextOffset: end, eof: end == bytes.count
        ))
    }
}
