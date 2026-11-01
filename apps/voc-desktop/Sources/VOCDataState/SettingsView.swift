import SwiftUI

/// The settings panel's two tabs. Stored, so "Ver detalles" in the notice and a
/// connection failure can choose the tab before the panel opens.
enum SettingsTab: String {
    case connection
    case agent

    static let storageKey = "voc.settings.tab"
}

/// ⌘, opens the connection and the agent's work in a floating panel, so the main
/// window holds only the page, as the web app does.
struct SettingsView: View {
    @ObservedObject var connection: ConnectionModel
    @ObservedObject var handoff: HandoffController
    @ObservedObject var reports: ReportController
    @ObservedObject var signIn: SignInController
    @AppStorage(SettingsTab.storageKey) private var tab = SettingsTab.connection.rawValue

    var body: some View {
        TabView(selection: $tab) {
            ConnectionSettings(connection: connection, handoff: handoff, reports: reports, signIn: signIn)
                .tabItem { Label("Conexión", systemImage: "network") }
                .tag(SettingsTab.connection.rawValue)
            AgentSettings(connection: connection, handoff: handoff, reports: reports)
                .tabItem { Label("Agente", systemImage: "sparkles") }
                .tag(SettingsTab.agent.rawValue)
        }
        .frame(width: 560)
        .tint(VOCTheme.accent)
        .preferredColorScheme(VOCTheme.appearance)
        .background(FloatingPanel())
    }
}

private struct ConnectionSettings: View {
    @ObservedObject var connection: ConnectionModel
    @ObservedObject var handoff: HandoffController
    @ObservedObject var reports: ReportController
    @ObservedObject var signIn: SignInController

    private var locked: Bool { handoff.isDelivering || reports.busy }

    var body: some View {
        Form {
            Section {
                HStack {
                    TextField("Servidor", text: $connection.server, prompt: Text("http://127.0.0.1:8091"))
                        .onSubmit { connection.connect() }
                        .accessibilityIdentifier("server-address")
                        .disabled(locked)
                    Button("Conectar") { connection.connect() }
                        .accessibilityIdentifier("connect-server")
                        .disabled(locked)
                }
                HStack(spacing: 8) {
                    if connection.isLoading { ProgressView().controlSize(.small) }
                    Text(connection.status)
                        .font(.callout)
                        .foregroundStyle(connection.hasError ? VOCTheme.negative : VOCTheme.inkMuted)
                        .textSelection(.enabled)
                        .accessibilityIdentifier("connection-status")
                }
            } header: {
                Text("Servidor")
            }
            Section {
                HStack {
                    TextField("Conversación", text: $connection.conversation, prompt: Text("UUID opcional; vacío para una nueva"))
                        .onSubmit { connection.connect() }
                        .accessibilityIdentifier("conversation-id")
                        .disabled(locked)
                    Button("Recargar") { connection.reload() }
                        .disabled(connection.command == nil || handoff.isDelivering)
                        .accessibilityIdentifier("reload-workspace")
                }
            } header: {
                Text("Conversación")
            } footer: {
                Text("Recargar también está en Visualización › Recargar la página (⌘R).")
                    .font(.caption).foregroundStyle(VOCTheme.inkMuted)
            }
            if signIn.mode != .local {
                Section("Sesión") {
                    HStack(spacing: 8) {
                        if signIn.mode == .unknown {
                            Text(signIn.message.isEmpty ? "Comprobando si el servidor pide inicio de sesión…" : signIn.message)
                                .font(.callout).foregroundStyle(VOCTheme.inkMuted).textSelection(.enabled)
                        } else if let email = signIn.email {
                            Text(email).textSelection(.enabled).accessibilityIdentifier("session-email")
                            Spacer()
                            Button("Cerrar sesión") {
                                Task { await signIn.signOut(origin: connection.currentOrigin); connection.reload() }
                            }
                            .disabled(signIn.busy || locked)
                            .accessibilityIdentifier("sign-out")
                        } else {
                            Text("Sin sesión").foregroundStyle(VOCTheme.inkMuted)
                            Spacer()
                            Button("Iniciar sesión") { Task { await signIn.signIn(origin: connection.currentOrigin) } }
                                .disabled(signIn.busy)
                                .accessibilityIdentifier("sign-in")
                        }
                        if signIn.busy { ProgressView().controlSize(.small) }
                    }
                    if signIn.mode == .cognito, !signIn.message.isEmpty {
                        Text(signIn.message).font(.callout).foregroundStyle(VOCTheme.inkMuted).textSelection(.enabled)
                    }
                }
            }
        }
        .formStyle(.grouped)
    }
}

private struct AgentSettings: View {
    @ObservedObject var connection: ConnectionModel
    @ObservedObject var handoff: HandoffController
    @ObservedObject var reports: ReportController

    var body: some View {
        Form {
            if handoff.message.isEmpty, handoff.work == nil {
                Section {
                    Text("Sin trabajos del agente. Cuando Claude Desktop abra una pregunta con voc_open, su estado aparece aquí.")
                        .foregroundStyle(VOCTheme.inkMuted)
                }
            } else {
                Section("Estado") {
                    Text(handoff.message).textSelection(.enabled)
                    if let work = handoff.work {
                        LabeledContent("Trabajo") {
                            Text(work.workID).font(.callout.monospaced()).textSelection(.enabled)
                        }
                        LabeledContent("Etapa", value: work.state.rawValue)
                        if [.opening, .failed].contains(work.state), work.reports?.isEmpty != false {
                            Button("Recuperar entrega") { handoff.recover() }
                                .disabled(handoff.isDelivering || reports.busy)
                        }
                    }
                }
                if let work = handoff.work, let conversation = work.conversationID {
                    Section {
                        LabeledContent("Conversación") {
                            Text(conversation).font(.callout.monospaced()).textSelection(.enabled)
                        }
                        HStack(spacing: 8) {
                            Button("Preparar resultado para el agente") {
                                Task { await reports.prepare(work, origin: connection.command?.profile.origin.absoluteString ?? "") }
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(handoff.isDelivering || reports.busy || work.state != .opened)
                            .accessibilityIdentifier("prepare-handoff-report")
                            if reports.busy { ProgressView().controlSize(.small) }
                        }
                        if let report = work.reports?.last {
                            LabeledContent("Informe") {
                                Text("\(report.reportID) · \(report.turnCount) turnos · \(report.byteLength) bytes")
                                    .font(.callout).textSelection(.enabled)
                            }
                            Button("Guardar Markdown…") { Task { await reports.save(work) } }
                                .disabled(reports.busy)
                                .accessibilityIdentifier("save-handoff-report")
                        }
                        if !reports.message.isEmpty {
                            Text(reports.message).font(.callout).textSelection(.enabled)
                        }
                    } header: {
                        Text("Resultado para el agente")
                    } footer: {
                        Text("Se exporta la conversación del trabajo, aunque hayas navegado a otra.")
                            .font(.caption).foregroundStyle(VOCTheme.inkMuted)
                    }
                }
            }
        }
        .formStyle(.grouped)
    }
}
