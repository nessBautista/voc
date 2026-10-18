import Foundation
import Darwin

/// GUI and bundled helper share private state in this non-sandboxed distribution.
enum DesktopFiles {
    static func directory() throws -> URL {
        let support = try FileManager.default.url(
            for: .applicationSupportDirectory, in: .userDomainMask,
            appropriateFor: nil, create: true
        )
        return support.appendingPathComponent("VOCDataState", isDirectory: true)
    }

    static func prepare(_ directory: URL) throws {
        try FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let values = try directory.resourceValues(forKeys: [.isSymbolicLinkKey, .isDirectoryKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else {
            throw HandoffError("unsafe_state_directory")
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
    }
}

/// A second GUI cannot recover a request while the original GUI is still delivering it.
final class DesktopLease {
    private let descriptor: Int32

    init(directory: URL) throws {
        try DesktopFiles.prepare(directory)
        descriptor = Darwin.open(
            directory.appendingPathComponent("gui.lock").path,
            O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0o600
        )
        guard descriptor >= 0 else { throw HandoffError("storage_unavailable") }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            close(descriptor)
            throw HandoffError("desktop_busy")
        }
    }

    deinit { flock(descriptor, LOCK_UN); close(descriptor) }
}
