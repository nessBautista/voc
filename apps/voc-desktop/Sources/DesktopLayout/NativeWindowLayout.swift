import AppKit
import ApplicationServices

/// Accessibility calls stay off the main actor, with short per-call deadlines and bounded lists.
actor AgentWindowAccess {
    private struct Row { let id: UInt32; let frame: CGRect }
    private struct Window { let element: AXUIElement; let frame: CGRect }

    func choices(profile: HandoffPresentation) throws -> [LayoutChoice] {
        try requirePermission()
        let applications = NSRunningApplication.runningApplications(withBundleIdentifier: profile.bundleID)
        guard applications.count <= 4 else {
            throw WindowLayoutError.unavailable("Hay demasiadas instancias de \(profile.applicationName). Cierra las que no usarás.")
        }
        var result: [LayoutChoice] = []
        for app in applications {
            guard let launched = app.launchDate, let url = app.bundleURL, !app.isHidden, !app.isTerminated else { continue }
            let visible = rows(pid: app.processIdentifier)
            let windows = try accessibilityWindows(app.processIdentifier)
            for row in visible {
                guard visible.filter({ SplitLayout.close($0.frame, row.frame) }).count == 1 else { continue }
                let matches = windows.filter { SplitLayout.close($0.frame, row.frame) }
                guard matches.count == 1, let window = matches.first, (try? validate(window.element)) != nil else { continue }
                let title = attribute(window.element, kAXTitleAttribute) as? String ?? profile.applicationName
                let target = LayoutTarget(profile: profile, processID: app.processIdentifier, launchedAt: launched,
                    applicationPath: url.resolvingSymlinksInPath().path, windowID: row.id)
                result.append(LayoutChoice(target: target,
                    title: "\(title.prefix(70)) · #\(row.id) · \(Int(row.frame.width)) × \(Int(row.frame.height)) pt"))
            }
        }
        return result.sorted { $0.target.windowID < $1.target.windowID }
    }

    func frame(target: LayoutTarget) throws -> CGRect { try frame(resolve(target)) }

    func move(_ desired: CGRect, target: LayoutTarget, expected: CGRect) throws {
        try Task.checkCancellation()
        let element = try resolve(target)
        guard SplitLayout.close(try frame(element), expected) else {
            throw WindowLayoutError.unavailable("La ventana elegida cambió de posición antes de aplicar. Se conserva tu cambio.")
        }
        var position = desired.origin, size = desired.size
        guard let point = AXValueCreate(.cgPoint, &position), let dimensions = AXValueCreate(.cgSize, &size) else {
            throw WindowLayoutError.unavailable("No se pudo preparar la geometría de la ventana.")
        }
        // A display boundary may clamp an initial move while the old size is still in effect.
        for (name, value) in [(kAXPositionAttribute, point), (kAXSizeAttribute, dimensions), (kAXPositionAttribute, point)] {
            try Task.checkCancellation()
            guard AXUIElementSetAttributeValue(element, name as CFString, value) == .success else {
                throw WindowLayoutError.unavailable("\(target.profile.applicationName) no aceptó una posición o tamaño.")
            }
        }
    }

    private func requirePermission() throws {
        guard AXIsProcessTrusted() else {
            throw WindowLayoutError.unavailable("Falta permiso de Accesibilidad para VOC. Puedes habilitarlo en Distribución de ventanas.")
        }
    }

    private func rows(pid: pid_t) -> [Row] {
        guard let info = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else { return [] }
        return info.compactMap { row in
            guard (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
                  (row[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
                  let number = row[kCGWindowNumber as String] as? NSNumber,
                  let raw = row[kCGWindowBounds as String] as? [String: Any],
                  let bounds = CGRect(dictionaryRepresentation: raw as CFDictionary),
                  bounds.width > 0, bounds.height > 0 else { return nil }
            return Row(id: number.uint32Value, frame: bounds)
        }
    }

    private func resolve(_ target: LayoutTarget) throws -> AXUIElement {
        try requirePermission()
        guard let app = NSRunningApplication(processIdentifier: target.processID), !app.isTerminated, !app.isHidden,
              app.bundleIdentifier == target.profile.bundleID, app.launchDate == target.launchedAt,
              app.bundleURL?.resolvingSymlinksInPath().path == target.applicationPath else {
            throw WindowLayoutError.unavailable("\(target.profile.applicationName) cambió de proceso o está oculto. Actualiza y vuelve a seleccionar su ventana.")
        }
        let visible = rows(pid: target.processID)
        guard let row = visible.first(where: { $0.id == target.windowID }) else {
            throw WindowLayoutError.unavailable("La ventana elegida está cerrada, minimizada o en otro escritorio.")
        }
        guard visible.filter({ SplitLayout.close($0.frame, row.frame) }).count == 1 else {
            throw WindowLayoutError.unavailable("Dos ventanas coinciden en posición y tamaño. Sepáralas antes de seleccionar.")
        }
        let matches = try accessibilityWindows(target.processID).filter { SplitLayout.close($0.frame, row.frame) }
        guard !matches.isEmpty else {
            throw WindowLayoutError.settling("La geometría de la ventana aún se está actualizando. Espera a que termine la animación y reintenta.")
        }
        guard matches.count == 1, let element = matches.first?.element else {
            throw WindowLayoutError.unavailable("No se pudo identificar una sola ventana de Accesibilidad.")
        }
        try validate(element)
        return element
    }

    private func validate(_ element: AXUIElement) throws {
        guard attribute(element, kAXSubroleAttribute) as? String == kAXStandardWindowSubrole,
              attribute(element, kAXMinimizedAttribute) as? Bool == false,
              attribute(element, "AXFullScreen") as? Bool == false else {
            throw WindowLayoutError.unavailable("Selecciona una ventana normal, visible y fuera de pantalla completa.")
        }
        for name in [kAXPositionAttribute, kAXSizeAttribute] {
            var settable: DarwinBoolean = false
            guard AXUIElementIsAttributeSettable(element, name as CFString, &settable) == .success, settable.boolValue else {
                throw WindowLayoutError.unavailable("La ventana elegida no permite cambiar su posición o tamaño.")
            }
        }
    }

    private func accessibilityWindows(_ pid: pid_t) throws -> [Window] {
        let application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.1)
        guard let windows = attribute(application, kAXWindowsAttribute) as? [AXUIElement], windows.count <= 16 else {
            throw WindowLayoutError.unavailable("No se pudo leer la lista de ventanas, o supera el límite de 16.")
        }
        return try windows.map { element in
            AXUIElementSetMessagingTimeout(element, 0.1)
            return Window(element: element, frame: try frame(element))
        }
    }

    private func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value
    }

    private func frame(_ element: AXUIElement) throws -> CGRect {
        guard let positionValue = attribute(element, kAXPositionAttribute), CFGetTypeID(positionValue) == AXValueGetTypeID(),
              let sizeValue = attribute(element, kAXSizeAttribute), CFGetTypeID(sizeValue) == AXValueGetTypeID() else {
            throw WindowLayoutError.unavailable("No se pudo leer la geometría de la ventana elegida.")
        }
        var position = CGPoint.zero, size = CGSize.zero
        guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &position),
              AXValueGetValue(sizeValue as! AXValue, .cgSize, &size),
              [position.x, position.y, size.width, size.height].allSatisfy({ $0.isFinite }), size.width > 0, size.height > 0 else {
            throw WindowLayoutError.unavailable("Las dimensiones de la ventana no son válidas.")
        }
        return CGRect(origin: position, size: size)
    }
}

/// Only VOC's own NSWindow is touched on the main actor.
@MainActor
final class NativeWindowLayout: WindowLayoutBackend {
    weak var window: NSWindow?
    let access = AgentWindowAccess()

    func snapshot(target: LayoutTarget) async throws -> LayoutSnapshot {
        let before = try context()
        let agent = try await access.frame(target: target)
        let after = try context()
        guard before.displays == after.displays, before.windowID == after.windowID else {
            throw WindowLayoutError.unavailable("Cambió la pantalla o la ventana de VOC durante la inspección.")
        }
        return LayoutSnapshot(target: target, displayID: after.screen.id, displays: after.displays,
            workArea: after.screen.visibleFrame, primaryTop: after.primaryTop, vocWindowID: after.windowID,
            voc: after.frame, agent: agent, minimum: after.minimum)
    }

    func moveAgent(_ frame: CGRect, target: LayoutTarget, expected: CGRect) async throws {
        try await access.move(frame, target: target, expected: expected)
    }

    func moveVOC(_ frame: CGRect, expected: LayoutSnapshot) throws {
        let current = try context()
        guard current.windowID == expected.vocWindowID, current.displays == expected.displays,
              SplitLayout.close(current.frame, expected.voc) else {
            throw WindowLayoutError.unavailable("VOC o la pantalla cambió durante la operación. Se conserva la posición actual.")
        }
        window?.setFrame(frame, display: true, animate: false)
    }

    func screenSummary() -> String {
        do {
            let data = try context()
            let split = try SplitLayout.make(workArea: data.screen.visibleFrame, minimum: data.minimum)
            return "Pantalla integrada: \(Int(data.screen.visibleFrame.width)) × \(Int(data.screen.visibleFrame.height)) pt. Cada mitad: \(Int(split.voc.width)) × \(Int(split.voc.height)) pt."
        } catch { return error.localizedDescription }
    }

    private struct Context {
        let screen: LayoutDisplay
        let displays: [LayoutDisplay]
        let primaryTop: CGFloat
        let windowID: Int
        let frame: CGRect
        let minimum: CGSize
    }
    private func context() throws -> Context {
        guard let window, window.isVisible, !window.isMiniaturized,
              !window.styleMask.contains(.fullScreen), window.isOnActiveSpace else {
            throw WindowLayoutError.unavailable("VOC debe estar visible, en el escritorio actual y fuera de pantalla completa.")
        }
        let displays = NSScreen.screens.map { screen in
            LayoutDisplay(id: (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0,
                          frame: screen.frame, visibleFrame: screen.visibleFrame)
        }
        guard let screen = displays.first(where: { CGDisplayIsBuiltin($0.id) != 0 }),
              CGDisplayIsInMirrorSet(screen.id) == 0, let primary = displays.first else {
            throw WindowLayoutError.unavailable("La pantalla integrada no está disponible o está duplicada. Abre la tapa y usa un escritorio extendido.")
        }
        return Context(screen: screen, displays: displays, primaryTop: primary.frame.maxY,
                       windowID: window.windowNumber, frame: window.frame, minimum: window.minSize)
    }
}
