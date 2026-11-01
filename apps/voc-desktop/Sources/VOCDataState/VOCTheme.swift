import AppKit
import SwiftUI

/// The web app's voc-design tokens, so the native frame and the page read as one
/// surface. The page is dark unless it sets `data-theme="light"`, so the app pins
/// its windows to dark (`appearance`); the light values stay for that theme.
enum VOCTheme {
    static let background = dynamic(light: 0xF7F8FA, dark: 0x0A0C0F)
    static let surface = dynamic(light: 0xFFFFFF, dark: 0x11141A)
    static let line = dynamic(light: 0xDFE3EA, dark: 0x222731)
    static let ink = dynamic(light: 0x0F1319, dark: 0xEDF0F4)
    static let inkMuted = dynamic(light: 0x4A5363, dark: 0x98A1B0)
    static let accent = dynamic(light: 0x0B6F90, dark: 0x4CC9F0)
    static let negative = dynamic(light: 0xC42B45, dark: 0xFF6B81)
    /// The page's own theme: dark, whatever the system's appearance.
    static let appearance = ColorScheme.dark

    private static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(rgb: isDark ? dark : light)
        })
    }
}

private extension NSColor {
    convenience init(rgb: UInt32) {
        self.init(
            srgbRed: CGFloat((rgb >> 16) & 0xFF) / 255,
            green: CGFloat((rgb >> 8) & 0xFF) / 255,
            blue: CGFloat(rgb & 0xFF) / 255,
            alpha: 1
        )
    }
}

/// Reaches the hosting window to give the settings window panel behavior: it stays
/// above the conversation while the app is active and hides when the app is not.
struct FloatingPanel: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            guard let window = view.window else { return }
            window.level = .floating
            window.hidesOnDeactivate = true
        }
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {}
}
