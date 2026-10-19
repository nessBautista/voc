import XCTest

@MainActor
final class WindowLayoutTests: XCTestCase {
    final class Backend: WindowLayoutBackend {
        var value: LayoutSnapshot
        var denial: String?
        var settleReads = 0
        var refusedAgentSize = false
        var movedByUser = false
        var agentWrites = 0
        var vocWrites = 0

        init(_ value: LayoutSnapshot) { self.value = value }
        func snapshot(target: LayoutTarget) async throws -> LayoutSnapshot {
            if let denial { throw WindowLayoutError.unavailable(denial) }
            guard target == value.target else { throw WindowLayoutError.unavailable("Process changed") }
            if settleReads > 0 { settleReads -= 1; throw WindowLayoutError.settling("Restoring animation") }
            return value
        }
        func moveAgent(_ frame: CGRect, target: LayoutTarget, expected: CGRect) async throws {
            guard target == value.target, SplitLayout.close(value.agent, expected) else {
                throw WindowLayoutError.unavailable("User moved agent")
            }
            agentWrites += 1
            var actual = frame
            if refusedAgentSize { actual.size.width = 900 }
            if movedByUser { actual.origin.x += 50 }
            set(agent: actual)
        }
        func moveVOC(_ frame: CGRect, expected: LayoutSnapshot) throws {
            guard SplitLayout.close(value.voc, expected.voc) else { throw WindowLayoutError.unavailable("User moved VOC") }
            vocWrites += 1
            set(voc: frame)
        }
        func set(voc: CGRect? = nil, agent: CGRect? = nil, displays: [LayoutDisplay]? = nil, target: LayoutTarget? = nil) {
            value = LayoutSnapshot(target: target ?? value.target, displayID: value.displayID, displays: displays ?? value.displays,
                workArea: value.workArea, primaryTop: value.primaryTop, vocWindowID: value.vocWindowID,
                voc: voc ?? value.voc, agent: agent ?? value.agent, minimum: value.minimum)
        }
    }

    func fixture(profile: HandoffPresentation = .stdio) -> Backend {
        let target = LayoutTarget(profile: profile, processID: 12, launchedAt: Date(timeIntervalSince1970: 1),
                                  applicationPath: "/Applications/\(profile.applicationName).app", windowID: 42)
        let area = CGRect(x: 0, y: 0, width: 1512, height: 949)
        let display = LayoutDisplay(id: 1, frame: CGRect(x: 0, y: 0, width: 1512, height: 982), visibleFrame: area)
        return Backend(LayoutSnapshot(target: target, displayID: 1, displays: [display], workArea: area, primaryTop: 982,
            vocWindowID: 100, voc: CGRect(x: 100, y: 20, width: 1100, height: 800),
            agent: CGRect(x: 900, y: 50, width: 600, height: 800), minimum: CGSize(width: 560, height: 560)))
    }

    func testMeasuredScreenAndCoordinateConversion() throws {
        let split = try SplitLayout.make(workArea: CGRect(x: 0, y: 0, width: 1512, height: 949), minimum: CGSize(width: 560, height: 560))
        XCTAssertEqual(split.voc, CGRect(x: 8, y: 8, width: 742, height: 933))
        XCTAssertEqual(split.agent, CGRect(x: 762, y: 8, width: 742, height: 933))
        XCTAssertFalse(split.voc.intersects(split.agent))
        XCTAssertEqual(SplitLayout.accessibility(split.agent, primaryTop: 982), CGRect(x: 762, y: 41, width: 742, height: 933))
        let offset = CGRect(x: -1512, y: -400, width: 1512, height: 949)
        let displaced = try SplitLayout.make(workArea: offset, minimum: .zero)
        XCTAssertEqual(displaced.voc.minX, -1504)
        XCTAssertEqual(SplitLayout.accessibility(displaced.voc, primaryTop: 900).minY, 359)
    }

    func testTooSmallAndNonfiniteScreensAreRejected() {
        for rect in [CGRect(x: 0, y: 0, width: 1100, height: 949), CGRect(x: 0, y: 0, width: 1512, height: 500),
                     CGRect(x: 0, y: 0, width: CGFloat.infinity, height: 949)] {
            XCTAssertThrowsError(try SplitLayout.make(workArea: rect, minimum: .zero))
        }
        XCTAssertThrowsError(try SplitLayout.make(workArea: CGRect(x: 0, y: 0, width: 1512, height: 949), minimum: CGSize(width: 760, height: 600)))
    }

    func testApplyAndRestoreBothProfilesWithObservedGeometry() async {
        for profile in HandoffPresentation.allCases {
            let backend = fixture(profile: profile)
            let initial = backend.value
            let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
            let applied = await coordinator.apply(target: initial.target)
            XCTAssertEqual(applied.status, .applied)
            XCTAssertEqual(backend.value.voc.width, 742)
            XCTAssertEqual(backend.value.agent.width, 742)
            XCTAssertEqual(backend.agentWrites, 1)
            XCTAssertEqual(backend.vocWrites, 1)
            let restored = await coordinator.restore()
            XCTAssertEqual(restored.status, .applied)
            XCTAssertEqual(backend.value, initial)
        }
    }

    func testRepeatedApplyPreservesOriginalRestorePoint() async {
        let backend = fixture()
        let initial = backend.value
        let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
        _ = await coordinator.apply(target: initial.target)
        _ = await coordinator.apply(target: initial.target)
        _ = await coordinator.restore()
        XCTAssertEqual(backend.value, initial)
    }

    func testUserMoveSkipsRestoreWithoutWriting() async {
        let backend = fixture()
        let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
        _ = await coordinator.apply(target: backend.value.target)
        backend.set(agent: backend.value.agent.offsetBy(dx: 20, dy: 0))
        let changed = backend.value
        let restored = await coordinator.restore()
        XCTAssertEqual(restored.status, .skipped)
        XCTAssertEqual(backend.value, changed)
        XCTAssertEqual(backend.agentWrites, 1)
        XCTAssertEqual(backend.vocWrites, 1)
    }

    func testDisplayChangeAndReplacedProcessSkipRestore() async {
        for changeProcess in [true, false] {
            let backend = fixture()
            let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
            _ = await coordinator.apply(target: backend.value.target)
            if changeProcess {
                let old = backend.value.target
                backend.set(target: LayoutTarget(profile: old.profile, processID: old.processID,
                    launchedAt: Date(timeIntervalSince1970: 2), applicationPath: old.applicationPath, windowID: old.windowID))
            } else {
                backend.set(displays: [LayoutDisplay(id: 2, frame: .zero, visibleFrame: .zero)])
            }
            let restored = await coordinator.restore()
            XCTAssertEqual(restored.status, .skipped)
            XCTAssertEqual(backend.agentWrites, 1)
            XCTAssertEqual(backend.vocWrites, 1)
        }
    }

    func testUnavailableWindowsNeverWrite() async {
        for refusal in ["Permission denied", "Minimized", "Fullscreen", "Another Space", "Ambiguous", "Closed", "Hidden"] {
            let backend = fixture()
            backend.denial = refusal
            let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
            let result = await coordinator.apply(target: backend.value.target)
            XCTAssertEqual(result.status, .skipped)
            XCTAssertEqual(backend.agentWrites + backend.vocWrites, 0)
        }
    }

    func testRestoreAnimationRetriesReadsButNotWrites() async {
        let backend = fixture()
        backend.settleReads = 2
        let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
        let result = await coordinator.apply(target: backend.value.target)
        XCTAssertEqual(result.status, .applied)
        XCTAssertEqual(backend.agentWrites, 1)
        XCTAssertEqual(backend.vocWrites, 1)
    }

    func testUnsettledGeometryHasBoundedRetries() async {
        let backend = fixture()
        backend.settleReads = 20
        let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
        let result = await coordinator.apply(target: backend.value.target)
        XCTAssertEqual(result.status, .skipped)
        XCTAssertEqual(backend.settleReads, 14)
        XCTAssertEqual(backend.agentWrites + backend.vocWrites, 0)
    }

    func testUnacceptedSizeIsNotReportedAsApplied() async {
        let backend = fixture()
        backend.refusedAgentSize = true
        let original = backend.value
        let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
        let result = await coordinator.apply(target: original.target)
        XCTAssertEqual(result.status, .failed)
        XCTAssertNil(coordinator.restoration)
        XCTAssertEqual(backend.value.voc, original.voc)
        // Unrecognized geometry is not overwritten during best-effort recovery.
        XCTAssertEqual(backend.agentWrites, 1)
        XCTAssertEqual(backend.value.agent.width, 900)
    }

    func testManualChangeDuringApplyIsNotUndone() async {
        let backend = fixture()
        backend.movedByUser = true
        let coordinator = WindowLayoutCoordinator(backend: backend, pause: {})
        let result = await coordinator.apply(target: backend.value.target)
        XCTAssertEqual(result.status, .failed)
        XCTAssertEqual(backend.agentWrites, 1)
        XCTAssertEqual(backend.value.agent.minX, 812)
    }

    func testPreferencesAreSeparateAndReloadWithoutEnablingOtherProfile() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = LayoutPreferenceStore(url: directory.appendingPathComponent("layout.json"))
        var preferences = try store.load()
        XCTAssertFalse(preferences.stdio.enabled)
        preferences.stdio = LayoutPreference(enabled: true, target: fixture().value.target)
        try store.save(preferences)
        XCTAssertEqual(try store.load(), preferences)
        XCTAssertFalse(try store.load().http.enabled)
        XCTAssertNil(try store.load().http.target)
        let attributes = try FileManager.default.attributesOfItem(atPath: store.url.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
    }
}
