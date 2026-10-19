import SwiftUI

struct WindowLayoutPanel: View {
    @ObservedObject var model: WindowLayoutModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Distribución de ventanas").font(.title2).bold()
            Text("VOC ocupa la mitad izquierda de la pantalla integrada. La ventana que elijas ocupa la derecha.")
                .font(.callout).foregroundStyle(.secondary)
            Picker("Perfil", selection: $model.profile) {
                ForEach(HandoffPresentation.allCases, id: \.self) { profile in
                    Text(profile.label).tag(profile)
                }
            }.disabled(model.busy)
            Text(model.screen).font(.caption).textSelection(.enabled)
            HStack {
                Text(model.trusted ? "Accesibilidad habilitada" : "Accesibilidad pendiente")
                Spacer()
                Button("Solicitar permiso") { model.requestPermission() }
                    .disabled(model.busy)
            }
            Text("El permiso permite a VOC mover solo la ventana elegida. No requiere grabación de pantalla.")
                .font(.caption).foregroundStyle(.secondary)
            Picker("Ventana", selection: Binding<LayoutTarget?>(
                get: { model.preference.target }, set: { model.select($0) }
            )) {
                Text("Seleccionar ventana…").tag(Optional<LayoutTarget>.none)
                if let target = model.preference.target, !model.choices.contains(where: { $0.target == target }) {
                    Text("Selección guardada #\(target.windowID) · actualiza para comprobarla").tag(Optional(target))
                }
                ForEach(model.choices) { choice in Text(choice.title).tag(Optional(choice.id)) }
            }.disabled(model.busy)
            Button("Actualizar ventanas") { Task { await model.refresh() } }
                .disabled(model.busy)
            Toggle("Organizar al abrir desde MCP", isOn: Binding(
                get: { model.preference.enabled }, set: { model.enable($0) }
            )).disabled(model.busy)
            Text("Esta preferencia corresponde al perfil seleccionado. Repetir una entrega no vuelve a mover ventanas.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Aplicar ahora") { Task { _ = await model.apply(profile: model.profile) } }
                    .disabled(model.busy || model.preference.target == nil)
                Button("Desactivar") { model.enable(false) }.disabled(model.busy)
            }
            Button("Restaurar posiciones anteriores") { Task { await model.restore() } }.disabled(model.busy)
            Text(model.message).font(.callout).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            HStack {
                if model.busy { ProgressView().controlSize(.small) }
                Spacer()
                Button("Listo") { dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(22)
        .frame(width: 510)
        .task(id: model.profile) { await model.refresh() }
    }
}

/// Capture the workspace itself; a sheet or another window must never become the layout target.
struct WorkspaceWindowReader: NSViewRepresentable {
    let attach: (NSWindow) -> Void
    func makeNSView(context: Context) -> Reader { Reader(attach: attach) }
    func updateNSView(_ view: Reader, context: Context) {}
    final class Reader: NSView {
        let attach: (NSWindow) -> Void
        init(attach: @escaping (NSWindow) -> Void) { self.attach = attach; super.init(frame: .zero) }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let window { attach(window) }
        }
    }
}
