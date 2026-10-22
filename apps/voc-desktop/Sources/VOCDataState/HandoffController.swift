import AppKit
import Combine

/// Recoveries are explicit. Opening the GUI never silently resends unfinished work.
@MainActor
final class HandoffController: ObservableObject {
    @Published private(set) var work: HandoffWork?
    @Published private(set) var message = ""
    @Published private(set) var isDelivering = false
    private let connection: ConnectionModel
    private let store: HandoffStore?
    private let client: any HandoffTransport
    private let owner = UUID().uuidString.lowercased()
    private var lease: DesktopLease?
    private var task: Task<Void, Never>?
    private var navigationID: UUID?
    /// Fired once when a delivery is parked for lack of a signed-in person.
    var onSignInRequired: (@MainActor () -> Void)?
    /// Fired when a delivered conversation is on screen.
    var onOpened: (@MainActor () -> Void)?

    init(connection: ConnectionModel, store: HandoffStore?, client: any HandoffTransport = HandoffClient(), startupError: String? = nil) {
        self.connection = connection
        self.store = store
        self.client = client
        if let startupError { message = startupError }
        else { refresh() }
        connection.onNavigationLoaded = { [weak self] id, url in self?.loaded(id: id, url: url) }
        connection.onNavigationFailed = { [weak self] id in self?.navigationFailed(id: id) }
    }

    func refresh() {
        do {
            if let store { work = try store.latest() }
            if let work { message = description(work) }
        } catch { message = error.localizedDescription }
    }

    func receive(_ url: URL) {
        do { begin(try HandoffWork.identity(from: url), recover: false) }
        catch { message = error.localizedDescription }
    }

    func recover() {
        guard let work else { return }
        begin(work.workID, recover: true)
    }

    /// Signing out forgets every prepared report on this Mac; the server keeps its own.
    func forgetPreparedReports() {
        try? store?.clearReportCache()
        refresh()
    }

    /// After a sign-in, submit a parked question once; anything else is left alone.
    func resumeAfterSignIn() {
        guard let work, work.state == .awaitingSignIn, !isDelivering else { return }
        begin(work.workID, recover: true)
    }

    private func begin(_ id: String, recover: Bool) {
        guard let store else { return }
        do {
            if lease == nil { lease = try DesktopLease(directory: store.directory) }
            let origin = try ConnectionProfile(server: connection.server).origin.absoluteString
            guard let claimed = try store.claim(id, origin: origin, owner: owner, recover: recover) else {
                refresh()
                return
            }
            // A newer delivery supersedes one still in flight; the store already released it.
            task?.cancel()
            work = claimed
            navigationID = nil
            isDelivering = true
            message = "Entregando la pregunta al servidor…"
            // Replayed or expired links never reach this activation point.
            WorkspacePresenter.present()
            task = Task { [weak self] in
                guard let self else { return }
                do {
                    var received = claimed
                    if received.conversationID == nil {
                        let receipt = try await self.client.send(claimed)
                        try Task.checkCancellation()
                        received = try store.received(id, owner: self.owner, receipt: receipt)
                    }
                    try Task.checkCancellation()
                    self.work = received
                    // Always navigate with the pinned origin, never an edited form value.
                    self.navigationID = try self.connection.showHandoff(received)
                    self.message = "Pregunta registrada. Abriendo su conversación…"
                } catch {
                    if !Task.isCancelled {
                        let code = (error as? HandoffError)?.code ?? "transport_failed"
                        if code == "sign_in_required", let parked = try? store.awaitSignIn(id, owner: self.owner) {
                            // The question is kept; it is sent exactly once after a sign-in.
                            self.work = parked
                            self.message = self.description(parked)
                            self.onSignInRequired?()
                        } else {
                            try? store.fail(id, owner: self.owner, code: code)
                            self.refresh()
                            self.message = error.localizedDescription
                        }
                    }
                    if self.work?.workID == id { self.isDelivering = false }
                }
            }
        } catch {
            refresh()
            message = error.localizedDescription
        }
    }

    private func loaded(id: UUID, url: URL?) {
        guard navigationID != nil, let work, let store, let url else { return }
        // A reload issued meanwhile, for example by the session restore, lands on the same page; accept it by its URL.
        guard id == navigationID || url == (try? ConnectionProfile(server: work.serverOrigin, conversation: work.conversationID ?? "").workspaceURL) else { return }
        do {
            self.work = try store.opened(work.workID, owner: owner, url: url)
            message = description(self.work!)
            onOpened?()
        } catch {
            try? store.fail(work.workID, owner: owner, code: "navigation_failed")
            refresh()
        }
        isDelivering = false
        navigationID = nil
    }

    private func navigationFailed(id: UUID) {
        // Only the current command reports; while a handoff waits for its page, any failure is its failure.
        guard navigationID != nil, let work, let store else { return }
        try? store.fail(work.workID, owner: owner, code: "navigation_failed")
        navigationID = nil
        isDelivering = false
        refresh()
    }

    private func description(_ work: HandoffWork) -> String {
        if work.reports?.isEmpty == false {
            return "Resultado preparado para el agente. La conversación se conserva en el servidor."
        }
        switch work.state {
        case .opening: return "Entrega pendiente. Recuperar continúa el mismo trabajo; no crea otra pregunta."
        case .awaitingSignIn: return "Esperando inicio de sesión. La pregunta se enviará una sola vez cuando inicies sesión en este servidor."
        case .opened: return "Conversación abierta. Vuelve al agente con Cmd+Tab cuando quieras."
        case .failed: return HandoffError(work.errorCode ?? "transport_failed").localizedDescription
        case .expired: return HandoffError("expired").localizedDescription
        case .abandoned: return HandoffError("abandoned").localizedDescription
        }
    }
}

/// A delivery brings VOC to the front, maximized on the screen the person is using. Nothing else moves.
@MainActor
enum WorkspacePresenter {
    struct Display: Equatable {
        let frame: CGRect
        let visibleFrame: CGRect
    }

    /// The screen under the pointer is where the agent window was; otherwise the window's own screen.
    static func target(mouse: CGPoint, displays: [Display], fallback: CGRect?) -> CGRect? {
        displays.first(where: { NSMouseInRect(mouse, $0.frame, false) })?.visibleFrame ?? fallback
    }

    static func present() {
        NSApp?.activate(ignoringOtherApps: true)
        guard let window = NSApp?.windows.first(where: { $0.identifier?.rawValue == "workspace" })
            ?? NSApp?.windows.first(where: { $0.isVisible }) else { return }
        if window.isMiniaturized { window.deminiaturize(nil) }
        window.makeKeyAndOrderFront(nil)
        guard !window.styleMask.contains(.fullScreen) else { return }
        let displays = NSScreen.screens.map { Display(frame: $0.frame, visibleFrame: $0.visibleFrame) }
        let fallback = (window.screen ?? NSScreen.main)?.visibleFrame
        if let frame = target(mouse: NSEvent.mouseLocation, displays: displays, fallback: fallback) {
            window.setFrame(frame, display: true, animate: false)
        }
    }
}
