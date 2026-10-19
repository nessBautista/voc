import Foundation

/// The trusted adapter chooses a local preference; MCP arguments never name windows.
enum HandoffPresentation: String, Codable, CaseIterable, Sendable {
    case stdio, http

    var applicationName: String { self == .stdio ? "Claude" : "Ghostty" }
    var bundleID: String { self == .stdio ? "com.anthropic.claudefordesktop" : "com.mitchellh.ghostty" }
    var label: String { self == .stdio ? "Claude Desktop · stdio" : "Ghostty · HTTP / Argo" }
}

struct HandoffLayoutResult: Codable, Equatable, Sendable {
    enum Status: String, Codable, Sendable {
        case notRequested = "not_requested"
        case applied, skipped, failed
    }
    let status: Status
    let message: String
    static let notRequested = Self(status: .notRequested, message: "Distribución no solicitada.")
}
