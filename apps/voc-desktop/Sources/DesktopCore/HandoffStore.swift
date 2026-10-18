import Foundation
import SQLite3

/// Short SQLite transactions coordinate processes; no transaction spans HTTP or Launch Services.
final class HandoffStore {
    let directory: URL
    private var database: OpaquePointer?
    private let lock = NSLock()
    private let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    init(directory: URL) throws {
        self.directory = directory
        try DesktopFiles.prepare(directory)
        let path = directory.appendingPathComponent("handoffs.sqlite3")
        if FileManager.default.fileExists(atPath: path.path),
           try path.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink == true {
            throw HandoffError("unsafe_state_directory")
        }
        guard sqlite3_open_v2(path.path, &database, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            sqlite3_close(database)
            database = nil
            throw HandoffError("storage_unavailable")
        }
        do {
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
            sqlite3_busy_timeout(database, 5000)
            try execute("PRAGMA journal_mode=WAL")
            try execute("PRAGMA synchronous=FULL")
            try transaction {
                let version = try scalar("PRAGMA user_version")
                guard version == "0" || version == "1" else { throw HandoffError("unsupported_state_version") }
                try execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
                try execute("CREATE TABLE IF NOT EXISTS works (id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, active INTEGER NOT NULL CHECK(active IN (0,1)), record TEXT NOT NULL)")
                try execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active_work ON works(active) WHERE active = 1")
                try execute("PRAGMA user_version=1")
            }
        } catch {
            sqlite3_close(database)
            database = nil
            throw error
        }
    }

    deinit { sqlite3_close(database) }

    func configure(origin: String) throws {
        let profile = try ConnectionProfile(server: origin)
        try transaction {
            try execute("INSERT INTO settings VALUES ('origin', ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", [profile.origin.absoluteString])
        }
    }

    func configuredOrigin() throws -> String {
        try transaction {
            guard let value = try scalar("SELECT value FROM settings WHERE name='origin'") else {
                throw HandoffError("not_configured")
            }
            return try ConnectionProfile(server: value).origin.absoluteString
        }
    }

    func submit(_ input: HandoffInput, now: Double = Date().timeIntervalSince1970) throws -> (work: HandoffWork, shouldOpen: Bool) {
        try transaction {
            if let existing = try record("SELECT record FROM works WHERE request_id=?", [input.requestID]) {
                guard existing.question == input.question else { throw HandoffError("request_conflict") }
                return (try expire(existing, now: now), false)
            }
            if let active = try record("SELECT record FROM works WHERE active=1"),
               try expire(active, now: now).active {
                throw HandoffError("busy")
            }
            guard let origin = try scalar("SELECT value FROM settings WHERE name='origin'") else {
                throw HandoffError("not_configured")
            }
            let profile = try ConnectionProfile(server: origin)
            let work = HandoffWork(
                workID: UUID().uuidString.lowercased(), requestID: input.requestID,
                question: input.question, serverOrigin: profile.origin.absoluteString,
                requestBody: input.body, createdAt: now, expiresAt: now + 60, updatedAt: now
            )
            // Persist opening before the sole dispatcher calls Launch Services.
            try execute("INSERT INTO works VALUES (?, ?, 1, ?)", [work.workID, work.requestID, try encode(work)])
            return (work, true)
        }
    }

    func read(_ id: String, now: Double = Date().timeIntervalSince1970) throws -> HandoffWork {
        _ = try HandoffInput.identity(id)
        return try transaction { try expire(required(id), now: now) }
    }

    func latest(now: Double = Date().timeIntervalSince1970) throws -> HandoffWork? {
        try transaction {
            guard let work = try record("SELECT record FROM works ORDER BY active DESC, rowid DESC LIMIT 1") else { return nil }
            return try expire(work, now: now)
        }
    }

    func claim(_ id: String, origin: String, owner: String, recover: Bool = false,
               now: Double = Date().timeIntervalSince1970) throws -> HandoffWork? {
        try transaction {
            var work = try expire(required(id), now: now)
            guard work.state != .expired else { return nil }
            guard work.state != .abandoned else { return nil }
            guard work.serverOrigin == origin,
                  try scalar("SELECT value FROM settings WHERE name='origin'") == work.serverOrigin else {
                throw HandoffError("origin_changed")
            }
            // Recovery is a human action under the GUI lease. Deep-link replay never reclaims.
            if !recover && (work.claimedBy != nil || work.state != .opening) { return nil }
            if let other = try record("SELECT record FROM works WHERE active=1"), other.workID != id,
               try expire(other, now: now).active { throw HandoffError("busy") }
            work.active = true
            work.claimedBy = owner
            work.state = .opening
            work.errorCode = nil
            work.updatedAt = now
            try save(work)
            return work
        }
    }

    func received(_ id: String, owner: String, receipt: HandoffReceipt) throws -> HandoffWork {
        try transaction {
            var work = try owned(id, owner: owner)
            guard work.conversationID == nil || work.conversationID == receipt.conversationID,
                  work.publicationID == nil || work.publicationID == receipt.publicationID else {
                throw HandoffError("invalid_response")
            }
            work.conversationID = receipt.conversationID
            work.publicationID = receipt.publicationID
            work.turnStatus = receipt.turnStatus
            work.updatedAt = Date().timeIntervalSince1970
            try save(work)
            return work
        }
    }

    func opened(_ id: String, owner: String, url: URL) throws -> HandoffWork {
        try transaction {
            var work = try owned(id, owner: owner)
            guard let conversation = work.conversationID else { throw HandoffError("invalid_response") }
            let profile = try ConnectionProfile(server: work.serverOrigin, conversation: conversation)
            guard url == profile.workspaceURL else { throw HandoffError("navigation_failed") }
            work.state = .opened
            work.errorCode = nil
            work.updatedAt = Date().timeIntervalSince1970
            try save(work)
            return work
        }
    }

    func fail(_ id: String, owner: String?, code: String) throws {
        try transaction {
            var work = try required(id)
            // A late launcher error or cancelled HTTP callback cannot undo a newer claim.
            guard work.active, work.claimedBy == owner, work.state == .opening else { return }
            work.state = .failed
            work.errorCode = code
            work.active = false
            work.updatedAt = Date().timeIntervalSince1970
            try save(work)
        }
    }

    func abandon(_ id: String) throws {
        try transaction {
            var work = try required(id)
            work.active = false
            work.claimedBy = nil
            work.state = .abandoned
            work.updatedAt = Date().timeIntervalSince1970
            try save(work)
        }
    }

    private func expire(_ original: HandoffWork, now: Double) throws -> HandoffWork {
        var work = original
        if work.state == .opening && work.claimedBy == nil && now >= work.expiresAt {
            work.state = .expired
            work.active = false
            work.errorCode = "expired"
            work.updatedAt = now
            try save(work)
        }
        return work
    }

    private func owned(_ id: String, owner: String) throws -> HandoffWork {
        let work = try required(id)
        guard work.active, work.claimedBy == owner, work.state == .opening else { throw HandoffError("inactive") }
        return work
    }

    private func required(_ id: String) throws -> HandoffWork {
        guard let work = try record("SELECT record FROM works WHERE id=?", [id]) else { throw HandoffError("not_found") }
        return work
    }

    private func save(_ work: HandoffWork) throws {
        try execute("UPDATE works SET active=?, record=? WHERE id=?", [work.active ? "1" : "0", try encode(work), work.workID])
    }

    private func encode(_ work: HandoffWork) throws -> String {
        String(decoding: try JSONEncoder().encode(work), as: UTF8.self)
    }

    private func record(_ sql: String, _ values: [String] = []) throws -> HandoffWork? {
        guard let json = try scalar(sql, values) else { return nil }
        return try JSONDecoder().decode(HandoffWork.self, from: Data(json.utf8))
    }

    private func transaction<T>(_ body: () throws -> T) throws -> T {
        lock.lock()
        defer { lock.unlock() }
        try execute("BEGIN IMMEDIATE")
        do {
            let result = try body()
            try execute("COMMIT")
            return result
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    private func statement(_ sql: String, _ values: [String]) throws -> OpaquePointer {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw HandoffError("storage_unavailable")
        }
        for (offset, value) in values.enumerated() {
            guard sqlite3_bind_text(statement, Int32(offset + 1), value, -1, transient) == SQLITE_OK else {
                sqlite3_finalize(statement)
                throw HandoffError("storage_unavailable")
            }
        }
        return statement
    }

    private func execute(_ sql: String, _ values: [String] = []) throws {
        let query = try statement(sql, values)
        defer { sqlite3_finalize(query) }
        var result = sqlite3_step(query)
        while result == SQLITE_ROW { result = sqlite3_step(query) }
        guard result == SQLITE_DONE else { throw HandoffError("storage_unavailable") }
    }

    private func scalar(_ sql: String, _ values: [String] = []) throws -> String? {
        let query = try statement(sql, values)
        defer { sqlite3_finalize(query) }
        let result = sqlite3_step(query)
        if result == SQLITE_DONE { return nil }
        guard result == SQLITE_ROW, let value = sqlite3_column_text(query, 0) else {
            throw HandoffError("storage_unavailable")
        }
        return String(cString: value)
    }
}
