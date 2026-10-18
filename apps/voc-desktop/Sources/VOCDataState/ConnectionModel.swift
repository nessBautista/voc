import Foundation
import Combine

@MainActor
final class ConnectionModel: ObservableObject {
    static let preferenceKey = "voc.data-state.server-origin"
    static let defaultServer = "http://127.0.0.1:8091"

    @Published var server: String
    @Published var conversation = ""
    @Published private(set) var command: WebCommand?
    @Published private(set) var status = "Introduce el servidor y conecta."
    @Published private(set) var isLoading = false
    @Published private(set) var hasError = false
    private let preferences: UserDefaults
    private let handoffStore: HandoffStore?
    var onNavigationLoaded: ((UUID, URL?) -> Void)?
    var onNavigationFailed: ((UUID) -> Void)?

    init(preferences: UserDefaults = .standard, handoffStore: HandoffStore? = nil) {
        self.handoffStore = handoffStore
        self.preferences = preferences
        // The app has its own bundle identity and stores only the server origin.
        server = (try? handoffStore?.configuredOrigin()) ?? preferences.string(forKey: Self.preferenceKey) ?? Self.defaultServer
    }

    func connect() {
        do {
            let profile = try ConnectionProfile(server: server, conversation: conversation)
            try handoffStore?.configure(origin: profile.origin.absoluteString)
            server = profile.origin.absoluteString
            conversation = profile.conversationID ?? ""
            preferences.set(server, forKey: Self.preferenceKey)
            begin(WebCommand(profile: profile, action: .connect))
        } catch {
            showError(error.localizedDescription)
        }
    }

    func showHandoff(_ work: HandoffWork) throws -> UUID {
        guard let identity = work.conversationID else { throw HandoffError("invalid_response") }
        let profile = try ConnectionProfile(server: work.serverOrigin, conversation: identity)
        server = profile.origin.absoluteString
        conversation = identity
        let next = WebCommand(profile: profile, action: .connect)
        begin(next)
        return next.id
    }

    func reload() {
        guard let command else { connect(); return }
        begin(WebCommand(profile: command.profile, action: .reload))
    }

    private func begin(_ next: WebCommand) {
        command = next
        isLoading = true
        hasError = false
        status = "Conectando…"
    }

    func loaded(commandID: UUID, url: URL? = nil) {
        guard command?.id == commandID else { return }
        isLoading = false
        hasError = false
        status = "Página cargada · \(command!.profile.origin.absoluteString)"
        onNavigationLoaded?(commandID, url)
    }

    func failed(_ message: String, commandID: UUID) {
        guard command?.id == commandID else { return }
        showError(message)
        onNavigationFailed?(commandID)
    }

    func showError(_ message: String) {
        isLoading = false
        hasError = true
        status = message
    }
}
