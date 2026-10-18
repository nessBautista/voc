import XCTest

final class ConnectionTests: XCTestCase {
    func testLoopbackAndHTTPSOrigins() throws {
        for address in ["http://127.0.0.1:8091", "http://localhost:8091", "http://[::1]:8091", "https://voc.example.com"] {
            let profile = try ConnectionProfile(server: address)
            XCTAssertTrue(profile.contains(profile.workspaceURL), address)
        }
        XCTAssertEqual(try ConnectionProfile(server: " HTTPS://VOC.EXAMPLE.COM:443/ ").origin.absoluteString, "https://voc.example.com")
    }

    func testInvalidAndPrivateURLMaterialIsRefused() {
        for address in ["", "localhost:8091", "file:///tmp/page.html", "javascript:alert(1)", "http://remote.example", "http://localhost.evil", "http://127.0.0.2", "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://user:password@127.0.0.1", "http://127.0.0.1/?token=secret", "http://127.0.0.1/#fragment", "http://127.0.0.1/api", "https://a b.example", "http://127.0.0.1\\@evil.example"] {
            XCTAssertThrowsError(try ConnectionProfile(server: address), address)
        }
    }

    func testConversationIdentityBuildsItsOwnQuery() throws {
        let identity = "Abcd0000-0000-4000-8000-000000000001"
        let profile = try ConnectionProfile(server: "http://127.0.0.1:8091", conversation: identity)
        XCTAssertEqual(profile.workspaceURL.absoluteString, "http://127.0.0.1:8091/?conversation=\(identity.lowercased())")
        for value in ["not-an-id", "../state", "id&token=secret"] {
            XCTAssertThrowsError(try ConnectionProfile(server: "http://localhost", conversation: value))
        }
    }

    func testNavigationKeepsTheSelectedOrigin() throws {
        let profile = try ConnectionProfile(server: "https://voc.example.com")
        XCTAssertEqual(profile.navigation(to: URL(string: "https://voc.example.com:443/?conversation=test"), userActivated: false), .allow)
        for address in ["https://elsewhere.example", "http://voc.example.com", "https://voc.example.com:444", "https://voc.example.com.evil"] {
            XCTAssertEqual(profile.navigation(to: URL(string: address), userActivated: false), .block)
            XCTAssertEqual(profile.navigation(to: URL(string: address), userActivated: true), .external)
        }
        for address in ["file:///tmp/file", "javascript:alert(1)", "voc://open", "https://user:password@elsewhere.example"] {
            XCTAssertEqual(profile.navigation(to: URL(string: address), userActivated: true), .block)
        }
        XCTAssertEqual(profile.navigation(to: nil, userActivated: true), .block)
    }

    func testSwiftUIRedrawDoesNotReloadAndExplicitRetryPreservesChat() throws {
        let profile = try ConnectionProfile(server: "http://127.0.0.1:8091")
        let connect = WebCommand(profile: profile, action: .connect)
        var tracker = NavigationTracker()
        XCTAssertEqual(tracker.target(for: connect, currentURL: nil), profile.workspaceURL)
        XCTAssertNil(tracker.target(for: connect, currentURL: profile.workspaceURL))
        let chat = URL(string: "http://127.0.0.1:8091/?conversation=00000000-0000-4000-8000-000000000001")!
        let reload = WebCommand(profile: profile, action: .reload)
        XCTAssertEqual(tracker.target(for: reload, currentURL: chat), chat)
        XCTAssertNil(tracker.target(for: reload, currentURL: chat))
        XCTAssertEqual(tracker.target(for: WebCommand(profile: profile, action: .connect), currentURL: chat), profile.workspaceURL)
    }

    func testRetryCannotLeaveTheServerAndSwitchSelectsNewOrigin() throws {
        let first = try ConnectionProfile(server: "http://127.0.0.1:8091")
        let second = try ConnectionProfile(server: "https://new.example")
        var tracker = NavigationTracker()
        XCTAssertEqual(tracker.target(for: WebCommand(profile: first, action: .reload), currentURL: second.workspaceURL), first.workspaceURL)
        XCTAssertEqual(tracker.target(for: WebCommand(profile: second, action: .connect), currentURL: first.workspaceURL), second.workspaceURL)
    }
}

@MainActor
final class ConnectionModelTests: XCTestCase {
    private func withModel(_ body: (ConnectionModel, UserDefaults) throws -> Void) rethrows {
        let suite = "dev.voc.data-state.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        try body(ConnectionModel(preferences: defaults), defaults)
    }

    func testOnlyOriginIsSavedAndConversationIsNotPersisted() async throws {
        try withModel { model, defaults in
            model.server = "https://voc.example.com/"
            model.conversation = "00000000-0000-4000-8000-000000000001"
            model.connect()
            XCTAssertEqual(defaults.string(forKey: ConnectionModel.preferenceKey), "https://voc.example.com")
            let restored = ConnectionModel(preferences: defaults)
            XCTAssertEqual(restored.server, "https://voc.example.com")
            XCTAssertEqual(restored.conversation, "")
            XCTAssertTrue(model.isLoading)
            XCTAssertNotNil(model.command)
        }
    }

    func testFailedInputDoesNotOverwriteSavedOrigin() async {
        withModel { model, defaults in
            model.connect()
            let id = model.command!.id
            model.server = "file:///tmp/page"
            model.connect()
            XCTAssertTrue(model.hasError)
            XCTAssertEqual(model.command!.id, id)
            XCTAssertEqual(defaults.string(forKey: ConnectionModel.preferenceKey), ConnectionModel.defaultServer)
        }
    }

    func testErrorRecoveryAndStaleCallbacks() async {
        withModel { model, _ in
            model.connect()
            let first = model.command!.id
            model.failed("Sin conexión", commandID: first)
            XCTAssertTrue(model.hasError)
            model.reload()
            let next = model.command!.id
            XCTAssertNotEqual(first, next)
            XCTAssertTrue(model.isLoading)
            model.failed("Fallo anterior", commandID: first)
            model.loaded(commandID: first)
            XCTAssertTrue(model.isLoading)
            model.loaded(commandID: next)
            XCTAssertFalse(model.isLoading)
            XCTAssertFalse(model.hasError)
        }
    }
}
