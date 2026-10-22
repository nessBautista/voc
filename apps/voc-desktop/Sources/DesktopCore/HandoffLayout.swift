import Foundation

/// The trusted adapter records which transport delivered a question; MCP arguments never choose it.
enum HandoffPresentation: String, Codable, CaseIterable, Sendable {
    case stdio, http
}
