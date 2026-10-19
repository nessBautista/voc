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
    private var presentation: HandoffPresentation?
    private var layoutTask: Task<Void, Never>?
    var onPresentation: (@MainActor (HandoffPresentation) async -> HandoffLayoutResult)?

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

    func abandon() {
        guard let store, let work else { return }
        do {
            try store.abandon(work.workID)
            task?.cancel()
            layoutTask?.cancel()
            presentation = nil
            navigationID = nil
            isDelivering = false
            refresh()
        } catch { message = error.localizedDescription }
    }

    private func begin(_ id: String, recover: Bool) {
        guard let store, !isDelivering else { return }
        do {
            if lease == nil { lease = try DesktopLease(directory: store.directory) }
            let origin = try ConnectionProfile(server: connection.server).origin.absoluteString
            guard let claimed = try store.claim(id, origin: origin, owner: owner, recover: recover) else {
                refresh()
                return
            }
            work = claimed
            presentation = recover ? nil : claimed.presentation
            navigationID = nil
            isDelivering = true
            message = "Entregando la pregunta al servidor…"
            // Replayed or expired links never reach this activation point.
            NSApp?.activate(ignoringOtherApps: true)
            NSApp?.windows.first(where: { $0.identifier?.rawValue == "workspace" || $0.isVisible })?.makeKeyAndOrderFront(nil)
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
                        try? store.fail(id, owner: self.owner, code: code)
                        self.refresh()
                        self.message = error.localizedDescription
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
        guard id == navigationID, let work, let store, let url else { return }
        do {
            self.work = try store.opened(work.workID, owner: owner, url: url)
            message = description(self.work!)
            if let presentation, let onPresentation,
               (try? store.reserveLayout(work.workID, owner: owner)) == true {
                layoutTask = Task { [weak self] in
                    let result = await onPresentation(presentation)
                    guard let self, !Task.isCancelled else { return }
                    do {
                        try store.finishLayout(work.workID, owner: self.owner, result: result)
                        self.refresh()
                    } catch { self.message = "La conversación está abierta, pero no se guardó el estado de distribución." }
                }
            }
        } catch {
            try? store.fail(work.workID, owner: owner, code: "navigation_failed")
            refresh()
        }
        isDelivering = false
        navigationID = nil
        presentation = nil
    }

    private func navigationFailed(id: UUID) {
        guard id == navigationID, let work, let store else { return }
        try? store.fail(work.workID, owner: owner, code: "navigation_failed")
        navigationID = nil
        isDelivering = false
        refresh()
    }

    private func description(_ work: HandoffWork) -> String {
        if work.reports?.isEmpty == false && !work.active {
            return "Resultado preparado para el agente. La conversación se conserva y puedes iniciar otro trabajo."
        }
        switch work.state {
        case .opening: return "Entrega pendiente. Recuperar continúa el mismo trabajo; no crea otra pregunta."
        case .opened: return "Conversación abierta. Continúa en el chat y prepara el resultado para el agente cuando termines."
        case .failed: return HandoffError(work.errorCode ?? "transport_failed").localizedDescription
        case .expired: return HandoffError("expired").localizedDescription
        case .abandoned: return HandoffError("abandoned").localizedDescription
        }
    }
}
