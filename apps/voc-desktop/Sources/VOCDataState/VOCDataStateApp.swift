import SwiftUI

@main
struct VOCDataStateApp: App {
    @StateObject private var connection: ConnectionModel
    @StateObject private var handoff: HandoffController

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
        _connection = StateObject(wrappedValue: connection)
        _handoff = StateObject(wrappedValue: HandoffController(
            connection: connection, store: store, startupError: startupError
        ))
    }

    var body: some Scene {
        Window("VOC · Estado de los datos", id: "workspace") {
            VStack(spacing: 0) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("Servidor").frame(width: 95, alignment: .leading)
                        TextField("http://127.0.0.1:8091", text: $connection.server)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit { connection.connect() }
                            .accessibilityIdentifier("server-address")
                            .disabled(handoff.isDelivering)
                        Button("Conectar") { connection.connect() }
                            .accessibilityIdentifier("connect-server")
                            .disabled(handoff.isDelivering)
                    }
                    HStack {
                        Text("Conversación").frame(width: 95, alignment: .leading)
                        TextField("UUID opcional; vacío para una conversación nueva", text: $connection.conversation)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit { connection.connect() }
                            .accessibilityIdentifier("conversation-id")
                            .disabled(handoff.isDelivering)
                        Button("Reintentar / Recargar") { connection.reload() }
                            .disabled(connection.command == nil || handoff.isDelivering)
                            .accessibilityIdentifier("reload-workspace")
                    }
                    HStack(spacing: 8) {
                        if connection.isLoading { ProgressView().controlSize(.small) }
                        Text(connection.status)
                            .font(.callout)
                            .foregroundStyle(connection.hasError ? Color.red : Color.secondary)
                            .textSelection(.enabled)
                            .accessibilityIdentifier("connection-status")
                    }
                }
                .padding(14)
                if !handoff.message.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(handoff.message).font(.callout).textSelection(.enabled)
                        if let work = handoff.work {
                            Text("Trabajo \(work.workID) · \(work.state.rawValue)")
                                .font(.caption).textSelection(.enabled)
                            HStack {
                                Button("Recuperar entrega") { handoff.recover() }
                                    .disabled(handoff.isDelivering || [.abandoned, .expired].contains(work.state))
                                Button("Abandonar trabajo") { handoff.abandon() }
                                    .disabled(work.state == .abandoned || work.state == .expired)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14).padding(.bottom, 10)
                }
                Divider()
                WebWorkspace(model: connection)
            }
            .frame(minWidth: 760, minHeight: 600)
            .onOpenURL { handoff.receive($0) }
            .onAppear {
                if connection.command == nil { connection.connect() }
            }
        }
        .defaultSize(width: 1180, height: 850)
    }
}
