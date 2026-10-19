// Read only VOC process identity and saved layout selection; never open or move windows.
import AppKit

let processes: [[String: Any]] = NSWorkspace.shared.runningApplications.compactMap { application in
    let executable = application.executableURL?.path ?? ""
    let identifier = application.bundleIdentifier ?? ""
    guard identifier.hasPrefix("dev.voc.data-state") ||
            ["VOCDataState", "VOCDesktopBridge"].contains(application.executableURL?.lastPathComponent ?? "") else { return nil }
    return [
        "pid": application.processIdentifier,
        "bundle_id": identifier,
        "bundle_path": application.bundleURL?.path ?? "",
        "executable_path": executable,
        "activation_policy": application.activationPolicy.rawValue,
        "finished_launching": application.isFinishedLaunching,
    ]
}
let directory = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                            appropriateFor: nil, create: false)
    .appendingPathComponent("VOCDataState")
let preferenceFile = directory.appendingPathComponent("window-layout.json")
var preferences: Any = NSNull()
if FileManager.default.fileExists(atPath: preferenceFile.path) {
    preferences = try JSONSerialization.jsonObject(with: Data(contentsOf: preferenceFile))
}
let output: [String: Any] = ["processes": processes, "layout_preferences": preferences]
let bytes = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
print(String(decoding: bytes, as: UTF8.self))
