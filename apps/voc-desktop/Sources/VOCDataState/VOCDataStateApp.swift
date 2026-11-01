import SwiftUI

@main
struct VOCDataStateApp: App {
    @StateObject private var connection: ConnectionModel
    @StateObject private var handoff: HandoffController
    @StateObject private var reports: ReportController
    @StateObject private var signIn: SignInController

    init() {
        let store: HandoffStore?
        let startupError: String?
        do {
            store = try HandoffStore(directory: DesktopFiles.directory())
            startupError = nil
        } catch {
            store = nil
            startupError = error.localizedDescription
        }
        let connection = ConnectionModel(handoffStore: store)
        let signIn = SignInController()
        _connection = StateObject(wrappedValue: connection)
        _signIn = StateObject(wrappedValue: signIn)
        // Native requests carry the signed-in person's token; a local server gets none.
        _reports = StateObject(wrappedValue: ReportController(store: store, client: HandoffReportClient(bearer: signIn.bearer)))
        _handoff = StateObject(wrappedValue: HandoffController(
            connection: connection, store: store, client: HandoffClient(bearer: signIn.bearer), startupError: startupError
        ))
    }

    var body: some Scene {
        // The page is the window: no title, and the connection and agent controls in ⌘,.
        Window("VOC", id: "workspace") {
            WorkspaceView(connection: connection, handoff: handoff, reports: reports, signIn: signIn)
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1180, height: 850)
        .commands {
            CommandGroup(before: .toolbar) {
                Button("Recargar la página") { connection.reload() }
                    .keyboardShortcut("r")
                Divider()
            }
        }

        Settings {
            SettingsView(connection: connection, handoff: handoff, reports: reports, signIn: signIn)
        }
    }
}
