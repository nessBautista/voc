import AppKit

@MainActor
protocol HandoffOpening {
    func open(_ work: HandoffWork) async throws
}

@MainActor
struct HandoffLauncher {
    let store: HandoffStore
    let opener: any HandoffOpening

    func submit(_ input: HandoffInput) async throws -> HandoffWork {
        let result = try store.submit(input)
        if result.shouldOpen {
            do { try await opener.open(result.work) }
            catch { try store.fail(result.work.workID, owner: nil, code: "launch_failed") }
        }
        return try store.read(result.work.workID)
    }
}

/// Only the application containing this helper can receive a launch request.
@MainActor
struct NativeHandoffOpening: HandoffOpening {
    let application: URL

    static func bundledApplication(executable: URL) throws -> URL {
        let executable = executable.resolvingSymlinksInPath()
        let application = executable.deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        let expected = application.appendingPathComponent("Contents/Helpers/VOCDesktopBridge")
        guard executable == expected, application.pathExtension == "app",
              Bundle(url: application)?.bundleIdentifier == "dev.voc.data-state" else {
            throw HandoffError("invalid_application")
        }
        return application
    }

    func open(_ work: HandoffWork) async throws {
        // The long-lived helper must never create its own Dock icon or become the foreground app.
        NSApplication.shared.setActivationPolicy(.prohibited)
        let configuration = NSWorkspace.OpenConfiguration()
        // The GUI activates itself after accepting a new, unexpired delivery.
        configuration.activates = false
        configuration.createsNewApplicationInstance = false
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            let attempt = OpeningAttempt(continuation: continuation)
            attempt.timeout = Task { @MainActor in
                do { try await Task.sleep(for: .seconds(10)) }
                catch { return }
                attempt.finish(HandoffError("launch_failed"))
            }
            NSWorkspace.shared.open([work.link], withApplicationAt: application, configuration: configuration) { _, error in
                Task { @MainActor in attempt.finish(error) }
            }
        }
    }
}

/// A late Launch Services callback cannot resume a timed-out MCP request twice.
@MainActor
final class OpeningAttempt {
    private var continuation: CheckedContinuation<Void, Error>?
    var timeout: Task<Void, Never>?

    init(continuation: CheckedContinuation<Void, Error>) { self.continuation = continuation }

    func finish(_ error: Error?) {
        guard let continuation else { return }
        self.continuation = nil
        timeout?.cancel()
        timeout = nil
        if let error { continuation.resume(throwing: error) }
        else { continuation.resume() }
    }
}
