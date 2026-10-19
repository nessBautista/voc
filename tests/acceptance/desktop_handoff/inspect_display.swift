// Read-only geometry probe. First narrow Claude manually to its minimum width.
import AppKit
import CoreGraphics

let screens = NSScreen.screens.map { screen -> [String: Any] in
    let id = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
    let area = screen.visibleFrame
    return ["built_in": CGDisplayIsBuiltin(id) != 0,
            "visible_width_points": area.width, "visible_height_points": area.height,
            "tile_width_points": floor((area.width - 16 - 12) / 2)]
}
let rows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
let applications = NSWorkspace.shared.runningApplications.filter {
    ($0.localizedName ?? "").lowercased().contains("claude") || $0.bundleIdentifier == "com.mitchellh.ghostty"
}.map { app -> [String: Any] in
    let windows = rows.compactMap { row -> [String: Any]? in
        guard (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == app.processIdentifier,
              (row[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
              let raw = row[kCGWindowBounds as String] as? [String: Any],
              let frame = CGRect(dictionaryRepresentation: raw as CFDictionary) else { return nil }
        return ["window_id": row[kCGWindowNumber as String] ?? 0,
                "width_points": frame.width, "height_points": frame.height]
    }
    return ["bundle_id": app.bundleIdentifier ?? "unknown", "windows": windows]
}
let data = try JSONSerialization.data(withJSONObject: ["screens": screens, "applications": applications], options: [.prettyPrinted, .sortedKeys])
print(String(decoding: data, as: UTF8.self))
