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

    init(preferences: UserDefaults = .standard) {
        self.preferences = preferences
        // The app has its own bundle identity and stores only the server origin.
        server = preferences.string(forKey: Self.preferenceKey) ?? Self.defaultServer
    }

    func connect() {
        do {
            let profile = try ConnectionProfile(server: server, conversation: conversation)
            server = profile.origin.absoluteString
            conversation = profile.conversationID ?? ""
            preferences.set(server, forKey: Self.preferenceKey)
            begin(WebCommand(profile: profile, action: .connect))
        } catch {
            showError(error.localizedDescription)
        }
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

    func loaded(commandID: UUID) {
        guard command?.id == commandID else { return }
        isLoading = false
        hasError = false
        status = "Página cargada · \(command!.profile.origin.absoluteString)"
    }

    func failed(_ message: String, commandID: UUID) {
        guard command?.id == commandID else { return }
        showError(message)
    }

    func showError(_ message: String) {
        isLoading = false
        hasError = true
        status = message
    }
}
