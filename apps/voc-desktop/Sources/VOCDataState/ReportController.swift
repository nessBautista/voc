import AppKit
import Combine
import UniformTypeIdentifiers

/// The user chooses when to freeze the work's conversation; reads never prepare it.
@MainActor
final class ReportController: ObservableObject {
    @Published private(set) var busy = false
    @Published private(set) var message = ""
    private let store: HandoffStore?
    private let client: any HandoffReportTransport
    var onChange: (() -> Void)?

    init(store: HandoffStore?, client: any HandoffReportTransport = HandoffReportClient()) {
        self.store = store
        self.client = client
    }

    func prepare(_ work: HandoffWork, origin: String) async {
        guard !busy, let store else { return }
        busy = true
        message = "Preparando la conversación del trabajo…"
        defer { busy = false }
        do {
            let selected = try store.reserveReport(work.workID, origin: origin)
            let report = try await client.prepare(selected.work, requestID: selected.requestID)
            try Task.checkCancellation()
            _ = try store.preparedReport(work.workID, requestID: selected.requestID, report: report)
            onChange?()
            message = "Resultado listo: \(report.turnCount) turnos. Vuelve al agente y pídele recuperar este trabajo."
        } catch { message = error.localizedDescription }
    }

    func save(_ work: HandoffWork) async {
        guard !busy, let report = work.reports?.last else { return }
        busy = true
        defer { busy = false }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.init(filenameExtension: "md") ?? .plainText]
        panel.nameFieldStringValue = "voc-\(report.reportID).md"
        panel.canCreateDirectories = true
        let result = await withCheckedContinuation { continuation in
            panel.begin { continuation.resume(returning: $0) }
        }
        guard result == .OK, let url = panel.url else {
            message = "Guardado cancelado. No se escribió ningún archivo."
            return
        }
        message = "Descargando y verificando Markdown…"
        do {
            let bytes = try await client.download(work, report: report)
            try Task.checkCancellation()
            // Save only completely verified bytes to the location selected by the user.
            try bytes.write(to: url, options: .atomic)
            message = "Markdown guardado; longitud y SHA-256 verificados."
        } catch { message = error.localizedDescription }
    }
}
