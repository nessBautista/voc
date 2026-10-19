import AppKit
import ApplicationServices
import Combine

@MainActor
final class WindowLayoutModel: ObservableObject {
    @Published var profile: HandoffPresentation = .stdio
    @Published private(set) var preferences = LayoutPreferences()
    @Published private(set) var choices: [LayoutChoice] = []
    @Published private(set) var busy = false
    @Published private(set) var trusted = false
    @Published private(set) var message = "Selecciona una ventana y prueba la distribución antes de activarla para MCP."
    @Published private(set) var screen = ""
    let backend = NativeWindowLayout()
    private lazy var coordinator = WindowLayoutCoordinator(backend: backend)
    private let store: LayoutPreferenceStore?

    init() {
        do {
            let url = try DesktopFiles.directory().appendingPathComponent("window-layout.json")
            let store = LayoutPreferenceStore(url: url)
            let loaded = try store.load()
            self.store = store
            preferences = loaded
        } catch {
            store = nil
            message = "No se pudieron leer las preferencias de distribución. \(error.localizedDescription)"
        }
    }

    var preference: LayoutPreference { preferences[profile] }

    func refresh() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        choices = []
        trusted = AXIsProcessTrusted()
        screen = backend.screenSummary()
        let requested = profile
        do {
            let found = try await backend.access.choices(profile: requested)
            guard profile == requested else { return }
            choices = found
            message = found.isEmpty
                ? "No hay ventanas normales disponibles. Abre \(profile.applicationName), comprueba Accesibilidad y actualiza."
                : "Selecciona explícitamente la ventana que acompañará a VOC. Actualizar no mueve ventanas."
        } catch { message = error.localizedDescription }
    }

    func select(_ target: LayoutTarget?) {
        var next = preferences
        next[profile].target = choices.first(where: { $0.target == target && $0.target.profile == profile })?.target
        persist(next)
    }

    func enable(_ enabled: Bool) {
        var next = preferences
        next[profile].enabled = enabled
        persist(next)
    }

    private func persist(_ next: LayoutPreferences) {
        do {
            guard let store else { throw HandoffError("storage_unavailable") }
            try store.save(next)
            preferences = next
        } catch { message = "No se guardó el cambio. \(error.localizedDescription)" }
    }

    func requestPermission() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        trusted = AXIsProcessTrustedWithOptions(options)
        message = "Habilita VOC · Estado de los datos en Ajustes del Sistema → Privacidad y seguridad → Accesibilidad. Después pulsa Actualizar ventanas."
    }

    func automatic(_ profile: HandoffPresentation) async -> HandoffLayoutResult {
        guard preferences[profile].enabled else { return .notRequested }
        return await apply(profile: profile)
    }

    func apply(profile: HandoffPresentation) async -> HandoffLayoutResult {
        guard !busy else { return .init(status: .skipped, message: "Hay otra operación de ventanas en curso.") }
        guard let target = preferences[profile].target, target.profile == profile else {
            let result = HandoffLayoutResult(status: .skipped, message: "Selecciona una ventana de \(profile.applicationName) en Distribución de ventanas.")
            message = result.message
            return result
        }
        busy = true
        defer { busy = false }
        let result = await coordinator.apply(target: target)
        message = result.message
        return result
    }

    func restore() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        message = await coordinator.restore().message
    }
}
