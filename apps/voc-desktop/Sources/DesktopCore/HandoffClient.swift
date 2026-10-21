import Foundation

struct HandoffReceipt: Equatable {
    let conversationID: String
    let publicationID: String
    let turnStatus: String

    static func decode(_ data: Data, for work: HandoffWork) throws -> HandoffReceipt {
        struct Turn: Decodable { let request_id: String; let question: String; let status: String }
        struct Conversation: Decodable {
            let schema_version: String
            let conversation_id: String
            let publication_id: String
            let turns: [Turn]
        }
        do {
            let conversation = try JSONDecoder().decode(Conversation.self, from: data)
            let matching = conversation.turns.filter { $0.request_id == work.requestID }
            guard conversation.schema_version == "voc-conversation-v1", matching.count == 1,
                  let turn = matching.first, turn.question == work.question,
                  ["running", "completed", "failed", "interrupted"].contains(turn.status) else {
                throw HandoffError("invalid_response")
            }
            return HandoffReceipt(
                conversationID: try HandoffInput.identity(conversation.conversation_id),
                publicationID: try HandoffInput.identity(conversation.publication_id),
                turnStatus: turn.status
            )
        } catch { throw HandoffError("invalid_response") }
    }
}

protocol HandoffTransport {
    func send(_ work: HandoffWork) async throws -> HandoffReceipt
}

/// Supplies a fresh access token for a server origin, or nil when that server has no login.
typealias BearerProvider = @Sendable (String) async throws -> String?

/// The native client submits the durable payload; the web app renders the saved answer.
final class HandoffClient: NSObject, HandoffTransport, URLSessionTaskDelegate {
    private let bearer: BearerProvider?
    private let sessionConfiguration: URLSessionConfiguration

    init(bearer: BearerProvider? = nil, configuration: URLSessionConfiguration = .ephemeral) {
        self.bearer = bearer
        sessionConfiguration = configuration
    }

    func send(_ work: HandoffWork) async throws -> HandoffReceipt {
        let profile = try ConnectionProfile(server: work.serverOrigin)
        let url = profile.origin.appendingPathComponent("api/v1/ask")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = Data(work.requestBody.utf8)
        if let token = try await bearer?(profile.origin.absoluteString) {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        let configuration = sessionConfiguration
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 25
        configuration.urlCache = nil
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        do {
            let (bytes, response) = try await session.bytes(for: request)
            guard let http = response as? HTTPURLResponse, http.url == url else { throw HandoffError("transport_failed") }
            // The server wants a signed-in person; the question is kept, not discarded.
            if http.statusCode == 401 { throw HandoffError("sign_in_required") }
            guard [200, 202].contains(http.statusCode) else { throw HandoffError("transport_failed") }
            var data = Data()
            for try await byte in bytes {
                guard data.count < 2 * 1024 * 1024 else { throw HandoffError("invalid_response") }
                data.append(byte)
            }
            return try HandoffReceipt.decode(data, for: work)
        } catch let error as HandoffError { throw error }
        catch { throw HandoffError("transport_failed") }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        // Never forward the user's question to a redirected origin or login page.
        completionHandler(nil)
    }
}
