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
        let configuration = NSWorkspace.OpenConfiguration()
        // The GUI activates itself after accepting a new, unexpired delivery.
        configuration.activates = false
        configuration.createsNewApplicationInstance = false
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            NSWorkspace.shared.open([work.link], withApplicationAt: application, configuration: configuration) { _, error in
                if let error { continuation.resume(throwing: error) }
                else { continuation.resume() }
            }
        }
    }
}
