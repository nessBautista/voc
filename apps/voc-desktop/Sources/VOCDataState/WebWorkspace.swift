import AppKit
import SwiftUI
import WebKit

struct WebWorkspace: NSViewRepresentable {
    @ObservedObject var model: ConnectionModel
    /// Nonpersistent store owned by the sign-in controller, which installs the session cookie.
    var dataStore: WKWebsiteDataStore = .nonPersistent()

    func makeCoordinator() -> Coordinator { Coordinator(model: model) }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        // Website data stays in memory. Conversation history belongs to the server.
        configuration.websiteDataStore = dataStore
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.allowsBackForwardNavigationGestures = false
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        guard let command = model.command,
              let target = context.coordinator.tracker.target(for: command, currentURL: view.url)
        else { return }
        context.coordinator.command = command
        view.stopLoading()
        let request = URLRequest(url: target, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 20)
        context.coordinator.navigation = view.load(request)
    }

    static func dismantleNSView(_ view: WKWebView, coordinator: Coordinator) {
        view.stopLoading()
        view.navigationDelegate = nil
        view.uiDelegate = nil
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        let model: ConnectionModel
        var tracker = NavigationTracker()
        var command: WebCommand?
        var navigation: WKNavigation?

        init(model: ConnectionModel) { self.model = model }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let command else { decisionHandler(.cancel); return }
            if let url = action.request.url, command.profile.contains(url), url.path == "/auth/login",
               action.targetFrame?.isMainFrame != false {
                // Sign-in happens in the system's authentication window, not in this view.
                decisionHandler(.cancel)
                model.onLoginRequested?()
                return
            }
            if let url = action.request.url, let host = url.host?.lowercased(), host == model.logoutHost,
               url.path == "/logout", action.targetFrame?.isMainFrame != false {
                // The page signed out; forget the native credentials too instead of visiting Cognito.
                decisionHandler(.cancel)
                model.onLogoutRequested?()
                return
            }
            let decision = command.profile.navigation(
                to: action.request.url, userActivated: action.navigationType == .linkActivated
            )
            switch decision {
            case .allow:
                decisionHandler(.allow)
            case .external:
                decisionHandler(.cancel)
                if let url = action.request.url { NSWorkspace.shared.open(url) }
            case .block:
                decisionHandler(.cancel)
                if action.targetFrame?.isMainFrame != false {
                    model.failed("Se bloqueó una navegación fuera del servidor configurado.", commandID: command.id)
                }
            }
        }

        func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                     decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            if response.isForMainFrame, let http = response.response as? HTTPURLResponse,
               http.statusCode >= 400, let command {
                model.failed("El servidor respondió HTTP \(http.statusCode). Revisa la dirección y reintenta.", commandID: command.id)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            guard self.navigation === navigation, let command else { return }
            model.loaded(commandID: command.id, url: webView.url)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            report(error, navigation: navigation)
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            report(error, navigation: navigation)
        }

        private func report(_ error: Error, navigation: WKNavigation?) {
            guard self.navigation === navigation, let command else { return }
            let failure = error as NSError
            // Reconnect cancels an older load; that cancellation is not a new failure.
            guard !(failure.domain == NSURLErrorDomain && failure.code == NSURLErrorCancelled) else { return }
            model.failed("No se pudo cargar la página. Comprueba el servidor y pulsa Reintentar / Recargar.", commandID: command.id)
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            guard let command else { return }
            model.failed("La vista web se cerró. Pulsa Reintentar / Recargar para recuperar la conversación.", commandID: command.id)
        }

        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            // A link requesting a new window uses this view or the user's browser.
            guard action.targetFrame == nil, let command, let url = action.request.url else { return nil }
            switch command.profile.navigation(to: url, userActivated: action.navigationType == .linkActivated) {
            case .allow:
                navigation = webView.load(action.request)
            case .external:
                // External links were already handed to the browser by the navigation policy.
                break
            case .block:
                break
            }
            return nil
        }
    }
}
