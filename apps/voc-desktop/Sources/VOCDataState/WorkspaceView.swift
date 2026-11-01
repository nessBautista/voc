import SwiftUI

/// The main window: the page alone, under a strip the color of the page's own
/// background where the window buttons sit. Connection and agent controls live in
/// the settings panel (⌘,); the agent's news shows here as a short notice.
struct WorkspaceView: View {
    @ObservedObject var connection: ConnectionModel
    @ObservedObject var handoff: HandoffController
    @ObservedObject var reports: ReportController
    @ObservedObject var signIn: SignInController
    @Environment(\.openSettings) private var openSettings
    @AppStorage(SettingsTab.storageKey) private var settingsTab = SettingsTab.connection.rawValue
    @State private var notice: String?
    @State private var dismissal: Task<Void, Never>?

    /// The height of the hidden title bar, where the window buttons are drawn.
    private let titleBarHeight: CGFloat = 28

    var body: some View {
        VStack(spacing: 0) {
            Color.clear.frame(height: titleBarHeight)
            WebWorkspace(model: connection, dataStore: signIn.webData)
        }
        .background(VOCTheme.background)
        .ignoresSafeArea()
        .overlay(alignment: .topTrailing) {
            if let notice {
                AgentNotice(
                    message: notice,
                    onDetails: { show(.agent) },
                    onClose: { dismiss() }
                )
                // Below the page's top bar, clear of its links and of the composer.
                .padding(.top, titleBarHeight + 64)
                .padding(.trailing, 20)
                .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.easeOut(duration: 0.16), value: notice)
        .frame(minWidth: 560, minHeight: 560)
        .preferredColorScheme(VOCTheme.appearance)
        .onOpenURL { handoff.receive($0) }
        .onAppear {
            reports.onChange = { handoff.refresh() }
            // A parked question asks for a sign-in; the page's own link does the same.
            handoff.onSignInRequired = { Task { await signIn.signIn(origin: connection.currentOrigin) } }
            connection.onLoginRequested = { Task { await signIn.signIn(origin: connection.currentOrigin) } }
            connection.onLogoutRequested = { Task { await signIn.signOut(origin: connection.currentOrigin); connection.reload() } }
            signIn.onSignedIn = { connection.logoutHost = signIn.logoutHost; connection.reload(); handoff.resumeAfterSignIn() }
            signIn.onSignedOut = { handoff.forgetPreparedReports() }
            if connection.command == nil { connection.connect() }
            // At launch only a startup problem is news; a saved work's state stays in the Agent tab.
            if handoff.work == nil, !handoff.message.isEmpty { announce(handoff.message) }
        }
        // A connection problem opens the panel on the connection, where it can be fixed.
        .onChange(of: connection.hasError) { _, failed in if failed { show(.connection) } }
        .onChange(of: handoff.message) { _, message in if !message.isEmpty { announce(message) } }
        .onChange(of: connection.command?.profile.origin) { _, origin in
            Task {
                await signIn.refresh(origin: origin)
                connection.logoutHost = signIn.logoutHost
                await signIn.restore(origin: origin)
            }
        }
    }

    private func show(_ tab: SettingsTab) {
        settingsTab = tab.rawValue
        openSettings()
    }

    /// The latest agent message, for a few seconds; the Agent tab keeps the full state.
    private func announce(_ message: String) {
        notice = message
        dismissal?.cancel()
        dismissal = Task {
            try? await Task.sleep(for: .seconds(8))
            if !Task.isCancelled { notice = nil }
        }
    }

    private func dismiss() {
        dismissal?.cancel()
        notice = nil
    }
}

/// A notice in the page's style: surface, hairline border and the page's type colors.
private struct AgentNotice: View {
    let message: String
    let onDetails: () -> Void
    let onClose: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("AGENTE")
                    .font(.system(size: 11, weight: .semibold))
                    .tracking(0.6)
                    .foregroundStyle(VOCTheme.inkMuted)
                Spacer()
                Button(action: onClose) {
                    Image(systemName: "xmark").font(.system(size: 11, weight: .semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(VOCTheme.inkMuted)
                .accessibilityLabel("Cerrar aviso")
            }
            Text(message)
                .font(.system(size: 13))
                .foregroundStyle(VOCTheme.ink)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Button("Ver detalles", action: onDetails)
                .buttonStyle(.plain)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(VOCTheme.accent)
                .accessibilityIdentifier("agent-notice-details")
        }
        .padding(14)
        .frame(width: 320, alignment: .leading)
        .background(VOCTheme.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(VOCTheme.line))
        .shadow(color: .black.opacity(0.35), radius: 12, y: 6)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("agent-notice")
    }
}
