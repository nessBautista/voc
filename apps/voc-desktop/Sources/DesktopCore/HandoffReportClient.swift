import Foundation

/// Only saved work origins and report identities determine requests; redirects are refused.
final class HandoffReportClient: NSObject, HandoffReportTransport, URLSessionTaskDelegate {
    private let bearer: BearerProvider?
    private let sessionConfiguration: URLSessionConfiguration

    init(bearer: BearerProvider? = nil, configuration: URLSessionConfiguration = .ephemeral) {
        self.bearer = bearer
        sessionConfiguration = configuration
    }

    func prepare(_ work: HandoffWork, requestID: String) async throws -> HandoffReport {
        guard let conversation = work.conversationID, let publication = work.publicationID else {
            throw HandoffError("report_not_ready")
        }
        _ = try HandoffInput.identity(requestID)
        _ = try HandoffInput.identity(conversation)
        let body = try JSONSerialization.data(withJSONObject: ["request_id": requestID, "publication_id": publication])
        let data = try await request(work, path: "api/v1/conversations/\(conversation)/reports", body: body)
        let report: HandoffReport
        do { report = try JSONDecoder().decode(HandoffReport.self, from: data) }
        catch { throw HandoffError("invalid_report") }
        try report.validate(for: work)
        return report
    }

    func page(_ work: HandoffWork, report: HandoffReport, offset: Int) async throws -> HandoffReportPage {
        try report.validate(for: work)
        guard (0...report.byteLength).contains(offset) else { throw HandoffError("invalid_report_offset") }
        let data = try await request(work, path: "api/v1/reports/\(report.reportID)/chunks", offset: offset)
        let result: HandoffReportPage
        do { result = try JSONDecoder().decode(HandoffReportPage.self, from: data) }
        catch { throw HandoffError("invalid_report") }
        _ = try result.bytes(expected: report, offset: offset)
        return result
    }

    private func request(_ work: HandoffWork, path: String, body: Data? = nil, offset: Int? = nil) async throws -> Data {
        let profile = try ConnectionProfile(server: work.serverOrigin)
        var parts = URLComponents(url: profile.origin.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if let offset { parts.queryItems = [URLQueryItem(name: "offset", value: String(offset))] }
        let url = parts.url!
        var request = URLRequest(url: url)
        request.httpMethod = body == nil ? "GET" : "POST"
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token = try await bearer?(profile.origin.absoluteString) {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let configuration = sessionConfiguration
        configuration.timeoutIntervalForRequest = 8
        configuration.timeoutIntervalForResource = 10
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        do {
            let (bytes, response) = try await session.bytes(for: request)
            guard let http = response as? HTTPURLResponse, http.url == url else { throw HandoffError("report_transport_failed") }
            if http.statusCode == 401 { throw HandoffError("sign_in_required") }
            var data = Data()
            for try await byte in bytes {
                guard data.count < 96 * 1024 else { throw HandoffError("invalid_report") }
                data.append(byte)
            }
            guard http.statusCode == 200 else {
                struct Failure: Decodable { struct Detail: Decodable { let code: String }; let error: Detail }
                let code = (try? JSONDecoder().decode(Failure.self, from: data))?.error.code
                let known = ["report_not_ready", "report_not_found", "report_too_large", "report_request_conflict", "report_publication_mismatch", "report_integrity_error"]
                throw HandoffError(code.map { known.contains($0) ? $0 : "report_transport_failed" } ?? "report_transport_failed")
            }
            return data
        } catch let error as HandoffError { throw error }
        catch { throw HandoffError("report_transport_failed") }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
