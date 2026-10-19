import Foundation
import CoreGraphics

/// Process lifetime and Quartz window number pin an explicit local selection.
struct LayoutTarget: Codable, Hashable, Sendable {
    let profile: HandoffPresentation
    let processID: Int32
    let launchedAt: Date
    let applicationPath: String
    let windowID: UInt32
}

struct LayoutChoice: Identifiable, Sendable {
    let target: LayoutTarget
    let title: String
    var id: LayoutTarget { target }
}

struct LayoutPreference: Codable, Equatable {
    var enabled = false
    var target: LayoutTarget?
}

struct LayoutPreferences: Codable, Equatable {
    var stdio = LayoutPreference()
    var http = LayoutPreference()
    subscript(_ profile: HandoffPresentation) -> LayoutPreference {
        get { profile == .stdio ? stdio : http }
        set { if profile == .stdio { stdio = newValue } else { http = newValue } }
    }
}

struct LayoutPreferenceStore {
    let url: URL
    func load() throws -> LayoutPreferences {
        guard FileManager.default.fileExists(atPath: url.path) else { return LayoutPreferences() }
        return try JSONDecoder().decode(LayoutPreferences.self, from: Data(contentsOf: url))
    }
    func save(_ preferences: LayoutPreferences) throws {
        try DesktopFiles.prepare(url.deletingLastPathComponent())
        try JSONEncoder().encode(preferences).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}

enum WindowLayoutError: LocalizedError {
    case unavailable(String)
    case settling(String)
    var errorDescription: String? {
        switch self { case .unavailable(let text), .settling(let text): return text }
    }
}

struct SplitLayout: Equatable {
    let voc: CGRect
    let agent: CGRect

    static func make(workArea: CGRect, minimum: CGSize) throws -> Self {
        let area = workArea.insetBy(dx: 8, dy: 8)
        let width = floor((area.width - 12) / 2)
        guard [area.minX, area.minY, area.width, area.height, minimum.width, minimum.height].allSatisfy({ $0.isFinite }),
              width >= max(560, minimum.width), area.height >= max(560, minimum.height) else {
            throw WindowLayoutError.unavailable("La pantalla integrada no permite dos mitades utilizables con la escala actual.")
        }
        return Self(voc: CGRect(x: area.minX, y: area.minY, width: width, height: area.height),
                    agent: CGRect(x: area.minX + width + 12, y: area.minY, width: width, height: area.height))
    }

    // AppKit uses bottom-left coordinates; Accessibility uses the primary display's top-left.
    static func accessibility(_ frame: CGRect, primaryTop: CGFloat) -> CGRect {
        CGRect(x: frame.minX, y: primaryTop - frame.maxY, width: frame.width, height: frame.height)
    }
    static func close(_ lhs: CGRect, _ rhs: CGRect) -> Bool {
        zip([lhs.minX, lhs.minY, lhs.width, lhs.height], [rhs.minX, rhs.minY, rhs.width, rhs.height])
            .allSatisfy { $0.isFinite && $1.isFinite && abs($0 - $1) <= 3 }
    }
}

struct LayoutDisplay: Equatable, Sendable {
    let id: UInt32
    let frame: CGRect
    let visibleFrame: CGRect
}

struct LayoutSnapshot: Equatable, Sendable {
    let target: LayoutTarget
    let displayID: UInt32
    let displays: [LayoutDisplay]
    let workArea: CGRect
    let primaryTop: CGFloat
    let vocWindowID: Int
    let voc: CGRect
    let agent: CGRect // Accessibility coordinates.
    let minimum: CGSize
}

@MainActor
protocol WindowLayoutBackend: AnyObject {
    func snapshot(target: LayoutTarget) async throws -> LayoutSnapshot
    func moveAgent(_ frame: CGRect, target: LayoutTarget, expected: CGRect) async throws
    func moveVOC(_ frame: CGRect, expected: LayoutSnapshot) throws
}

/// Writes happen once; bounded retries only wait for window metadata to settle.
@MainActor
final class WindowLayoutCoordinator {
    let backend: any WindowLayoutBackend
    private(set) var restoration: (before: LayoutSnapshot, after: LayoutSnapshot)?
    private var busy = false
    private let pause: @MainActor () async throws -> Void

    init(backend: any WindowLayoutBackend, pause: @escaping @MainActor () async throws -> Void = {
        try await Task.sleep(for: .milliseconds(100))
    }) {
        self.backend = backend
        self.pause = pause
    }

    private func settled<T>(_ operation: () async throws -> T) async throws -> T {
        let deadline = ContinuousClock.now.advanced(by: .seconds(1))
        for attempt in 0..<6 {
            try Task.checkCancellation()
            do { return try await operation() }
            catch let error as WindowLayoutError {
                guard case .settling = error, attempt < 5, ContinuousClock.now < deadline else { throw error }
                try await pause()
            }
        }
        throw WindowLayoutError.unavailable("Las ventanas no estabilizaron sus dimensiones.")
    }

    private func verified(display: LayoutSnapshot, voc: CGRect, agent: CGRect) async throws -> LayoutSnapshot {
        try await settled {
            let current = try await self.backend.snapshot(target: display.target)
            guard self.sameDisplay(display, current), display.vocWindowID == current.vocWindowID else {
                throw WindowLayoutError.unavailable("La pantalla o la ventana de VOC cambió durante la distribución.")
            }
            guard SplitLayout.close(current.voc, voc), SplitLayout.close(current.agent, agent) else {
                throw WindowLayoutError.settling("Una ventana no aceptó el tamaño solicitado. Revisa su tamaño mínimo y la escala de pantalla.")
            }
            return current
        }
    }

    func apply(target: LayoutTarget) async -> HandoffLayoutResult {
        guard !busy else { return .init(status: .skipped, message: "Hay una operación de ventanas en curso.") }
        busy = true
        defer { busy = false }
        do {
            let before = try await settled { try await self.backend.snapshot(target: target) }
            let split = try SplitLayout.make(workArea: before.workArea, minimum: before.minimum)
            let desired = SplitLayout.accessibility(split.agent, primaryTop: before.primaryTop)
            let previous = restoration
            restoration = nil
            do {
                // The adapter rechecks identity and the expected frame immediately before writing.
                try await backend.moveAgent(desired, target: target, expected: before.agent)
                try Task.checkCancellation()
                try backend.moveVOC(split.voc, expected: before)
                let after = try await verified(display: before, voc: split.voc, agent: desired)
                let unchanged = previous.map { matches($0.after, before) } ?? false
                restoration = (unchanged ? previous!.before : before, after)
                return .init(status: .applied, message: "Aplicado: VOC a la izquierda y \(target.profile.applicationName) a la derecha, en la pantalla integrada.")
            } catch {
                // A partial write may be restored only while it still matches our requested geometry.
                if !Task.isCancelled, let current = try? await backend.snapshot(target: target), sameDisplay(before, current) {
                    let positionOnly = CGRect(origin: desired.origin, size: before.agent.size)
                    let sizeOnly = CGRect(origin: before.agent.origin, size: desired.size)
                    if [desired, positionOnly, sizeOnly].contains(where: { SplitLayout.close(current.agent, $0) }) {
                        try? await backend.moveAgent(before.agent, target: target, expected: current.agent)
                    }
                    if SplitLayout.close(current.voc, split.voc) { try? backend.moveVOC(before.voc, expected: current) }
                }
                return .init(status: .failed, message: "No se confirmó la distribución; revisa las posiciones actuales. VOC sigue disponible. \(error.localizedDescription)")
            }
        } catch {
            return .init(status: .skipped, message: "Distribución omitida: \(error.localizedDescription)")
        }
    }

    func restore() async -> HandoffLayoutResult {
        guard !busy else { return .init(status: .skipped, message: "Hay una operación de ventanas en curso.") }
        busy = true
        defer { busy = false }
        guard let saved = restoration else {
            return .init(status: .skipped, message: "No hay posiciones anteriores guardadas en esta ejecución de VOC.")
        }
        restoration = nil
        var started = false
        do {
            let current = try await settled { try await self.backend.snapshot(target: saved.after.target) }
            guard matches(current, saved.after) else {
                return .init(status: .skipped, message: "Restauración omitida: cambió una ventana o pantalla. Se conserva tu distribución actual.")
            }
            started = true
            try await backend.moveAgent(saved.before.agent, target: saved.before.target, expected: current.agent)
            try Task.checkCancellation()
            try backend.moveVOC(saved.before.voc, expected: current)
            _ = try await verified(display: saved.before, voc: saved.before.voc, agent: saved.before.agent)
            return .init(status: .applied, message: "Posiciones anteriores restauradas.")
        } catch {
            return .init(status: started ? .failed : .skipped,
                         message: "Restauración \(started ? "incompleta" : "omitida"): \(error.localizedDescription)")
        }
    }

    private func sameDisplay(_ a: LayoutSnapshot, _ b: LayoutSnapshot) -> Bool {
        a.displayID == b.displayID && a.displays == b.displays && a.workArea == b.workArea && a.primaryTop == b.primaryTop
    }
    private func matches(_ a: LayoutSnapshot, _ b: LayoutSnapshot) -> Bool {
        a.target == b.target && a.vocWindowID == b.vocWindowID && sameDisplay(a, b)
            && SplitLayout.close(a.voc, b.voc) && SplitLayout.close(a.agent, b.agent)
    }
}
