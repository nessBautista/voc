import SwiftUI

@main
struct VOCDataStateApp: App {
    @StateObject private var connection: ConnectionModel
    @StateObject private var handoff: HandoffController
    @StateObject private var reports: ReportController
    @StateObject private var signIn: SignInController
    @StateObject private var layout = WindowLayoutModel()
    @State private var showLayout = false
    @State private var showConnection = true

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
        Window("VOC · Estado de los datos", id: "workspace") {
            VStack(spacing: 0) {
                HStack {
                    Text("VOC · Estado de los datos").font(.headline)
                    Spacer()
                    Button("Distribución de ventanas…") { showLayout = true }
                }.padding(.horizontal, 14).padding(.top, 10)
                DisclosureGroup("Conexión", isExpanded: $showConnection) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Text("Servidor").frame(width: 95, alignment: .leading)
                            TextField("http://127.0.0.1:8091", text: $connection.server)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { connection.connect() }
                                .accessibilityIdentifier("server-address")
                                .disabled(handoff.isDelivering || reports.busy)
                            Button("Conectar") { connection.connect() }
                                .accessibilityIdentifier("connect-server")
                                .disabled(handoff.isDelivering || reports.busy)
                        }
                        HStack {
                            Text("Conversación").frame(width: 95, alignment: .leading)
                            TextField("UUID opcional; vacío para una conversación nueva", text: $connection.conversation)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { connection.connect() }
                                .accessibilityIdentifier("conversation-id")
                                .disabled(handoff.isDelivering || reports.busy)
                            Button("Reintentar / Recargar") { connection.reload() }
                                .disabled(connection.command == nil || handoff.isDelivering)
                                .accessibilityIdentifier("reload-workspace")
                        }
                        if signIn.mode != .local {
                            HStack(spacing: 8) {
                                Text("Sesión").frame(width: 95, alignment: .leading)
                                if signIn.mode == .unknown {
                                    Text(signIn.message.isEmpty ? "Comprobando si el servidor pide inicio de sesión…" : signIn.message)
                                        .font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                                } else if let email = signIn.email {
                                    Text(email).font(.callout).textSelection(.enabled).accessibilityIdentifier("session-email")
                                    Button("Cerrar sesión") { Task { await signIn.signOut(origin: connection.currentOrigin); connection.reload() } }
                                        .disabled(signIn.busy || handoff.isDelivering || reports.busy)
                                        .accessibilityIdentifier("sign-out")
                                } else {
                                    Button("Iniciar sesión") { Task { await signIn.signIn(origin: connection.currentOrigin) } }
                                        .disabled(signIn.busy)
                                        .accessibilityIdentifier("sign-in")
                                }
                                if signIn.busy { ProgressView().controlSize(.small) }
                                if signIn.mode == .cognito, !signIn.message.isEmpty {
                                    Text(signIn.message).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                                }
                            }
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
                }.padding(12)
                if !handoff.message.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(handoff.message).font(.callout).textSelection(.enabled)
                        if let work = handoff.work {
                            Text("Trabajo \(work.workID) · \(work.state.rawValue)")
                                .font(.caption).textSelection(.enabled)
                            if let result = work.layoutResult {
                                Text(result.message).font(.caption).textSelection(.enabled)
                            }
                            if let conversation = work.conversationID {
                                DisclosureGroup("Resultado para el agente") {
                                    VStack(alignment: .leading, spacing: 6) {
                                        Text("Se exportará la conversación del trabajo: \(conversation)")
                                            .font(.caption).textSelection(.enabled)
                                        Text("Si navegaste a otra conversación, este botón conserva la del trabajo.")
                                            .font(.caption).foregroundStyle(.secondary)
                                        Button("Preparar resultado para el agente") {
                                            Task { await reports.prepare(work, origin: connection.command?.profile.origin.absoluteString ?? "") }
                                        }
                                        .disabled(handoff.isDelivering || reports.busy || work.state != .opened)
                                        .accessibilityIdentifier("prepare-handoff-report")
                                        if let report = work.reports?.last {
                                            Text("Informe \(report.reportID) · \(report.turnCount) turnos · \(report.byteLength) bytes")
                                                .font(.caption).textSelection(.enabled)
                                            Button("Guardar Markdown…") { Task { await reports.save(work) } }
                                                .disabled(reports.busy).accessibilityIdentifier("save-handoff-report")
                                        }
                                        if reports.busy { ProgressView().controlSize(.small) }
                                        if !reports.message.isEmpty { Text(reports.message).font(.callout).textSelection(.enabled) }
                                    }.frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                            HStack {
                                Button("Recuperar entrega") { handoff.recover() }
                                    .disabled(handoff.isDelivering || reports.busy || work.reports?.isEmpty == false || [.abandoned, .expired].contains(work.state))
                                Button("Abandonar trabajo") { handoff.abandon() }
                                    .disabled(reports.busy || !work.active)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 14).padding(.bottom, 10)
                }
                Divider()
                WebWorkspace(model: connection, dataStore: signIn.webData)
            }
            .frame(minWidth: 560, minHeight: 560)
            .background(WorkspaceWindowReader { layout.backend.window = $0 })
            .sheet(isPresented: $showLayout) { WindowLayoutPanel(model: layout) }
            .onOpenURL { handoff.receive($0) }
            .onAppear {
                reports.onChange = { handoff.refresh() }
                handoff.onPresentation = { profile in
                    showConnection = false
                    return await layout.automatic(profile)
                }
                // A parked question asks for a sign-in; the page's own link does the same.
                handoff.onSignInRequired = { showConnection = true; Task { await signIn.signIn(origin: connection.currentOrigin) } }
                connection.onLoginRequested = { Task { await signIn.signIn(origin: connection.currentOrigin) } }
                connection.onLogoutRequested = { Task { await signIn.signOut(origin: connection.currentOrigin); connection.reload() } }
                signIn.onSignedIn = { connection.logoutHost = signIn.logoutHost; connection.reload(); handoff.resumeAfterSignIn() }
                signIn.onSignedOut = { handoff.forgetPreparedReports() }
                if connection.command == nil { connection.connect() }
            }
            .onChange(of: connection.command?.profile.origin) { _, origin in
                Task {
                    await signIn.refresh(origin: origin)
                    connection.logoutHost = signIn.logoutHost
                    await signIn.restore(origin: origin)
                }
            }
        }
        .defaultSize(width: 1180, height: 850)
    }
}
