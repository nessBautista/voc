import SwiftUI

@main
struct VOCDataStateApp: App {
    @StateObject private var connection = ConnectionModel()

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
                        Button("Conectar") { connection.connect() }
                            .accessibilityIdentifier("connect-server")
                    }
                    HStack {
                        Text("Conversación").frame(width: 95, alignment: .leading)
                        TextField("UUID opcional; vacío para una conversación nueva", text: $connection.conversation)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit { connection.connect() }
                            .accessibilityIdentifier("conversation-id")
                        Button("Reintentar / Recargar") { connection.reload() }
                            .disabled(connection.command == nil)
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
                Divider()
                WebWorkspace(model: connection)
            }
            .frame(minWidth: 760, minHeight: 600)
            .onAppear {
                if connection.command == nil { connection.connect() }
            }
        }
        .defaultSize(width: 1180, height: 850)
    }
}
