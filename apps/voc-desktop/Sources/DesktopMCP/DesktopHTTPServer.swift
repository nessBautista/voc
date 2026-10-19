import Foundation
import MCP
@preconcurrency import NIOCore
@preconcurrency import NIOHTTP1
@preconcurrency import NIOPosix

/// NIO owns the loopback socket; the MCP SDK owns initialization, framing and tool dispatch.
@MainActor
final class DesktopHTTPServer {
    private let token: String
    private let sessions: DesktopHTTPSessions
    private var group: MultiThreadedEventLoopGroup?
    private var listener: Channel?
    private let connections = DesktopHTTPConnections()
    private var cleanup: Task<Void, Never>?

    init(token: String?, store: HandoffStore, opener: any HandoffOpening,
         sessionLimit: Int = 16, requestLimit: Int = 128, idleSeconds: TimeInterval = 600) throws {
        _ = try DesktopHTTPPolicy(token: token)
        self.token = token!
        sessions = DesktopHTTPSessions(store: store, opener: opener, sessionLimit: sessionLimit,
                                       requestLimit: requestLimit, idleSeconds: idleSeconds)
    }

    // Port zero is used only by native tests; the executable always chooses 8766.
    func start(port: Int = DesktopHTTPPolicy.port) async throws -> Int {
        guard group == nil else { throw HandoffError("http_already_started") }
        let group = MultiThreadedEventLoopGroup(numberOfThreads: 1)
        self.group = group
        let token = token, sessions = sessions, connections = connections
        let limits: NIOHTTPDecoderLimitConfiguration = {
            var value = NIOHTTPDecoderLimitConfiguration()
            value.maxHeaderFieldSize = 8192
            value.maxHeaderListSize = 16384
            value.maxHeaderFieldCount = 32
            return value
        }()
        do {
            listener = try await ServerBootstrap(group: group)
                .serverChannelOption(ChannelOptions.backlog, value: 32)
                .serverChannelOption(ChannelOptions.socketOption(.so_reuseaddr), value: 1)
                .childChannelInitializer { channel in
                    guard connections.admit(channel) else { return channel.close() }
                    let actualPort = channel.localAddress!.port!
                    let policy = try! DesktopHTTPPolicy(token: token, port: actualPort)
                    return channel.pipeline.configureHTTPServerPipeline(
                        withPipeliningAssistance: false, withDecoderLimitConfiguration: limits
                    ).flatMap {
                        channel.pipeline.addHandler(DesktopHTTPHandler(policy: policy, sessions: sessions))
                    }
                }
                .bind(host: "127.0.0.1", port: port).get()
        } catch {
            await stop()
            throw HandoffError("http_bind_failed")
        }
        cleanup = Task {
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                await sessions.expireIdle()
            }
        }
        return listener!.localAddress!.port!
    }

    func stop() async {
        cleanup?.cancel()
        cleanup = nil
        try? await listener?.close().get()
        listener = nil
        for channel in connections.all() { try? await channel.close().get() }
        await sessions.stop()
        if let group { try? await group.shutdownGracefully() }
        group = nil
    }

    func waitForSignal() async {
        // Foreground-only development server. Ctrl+C never stops the GUI or Claude's stdio process.
        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        let sources = [SIGINT, SIGTERM].map { DispatchSource.makeSignalSource(signal: $0, queue: .main) }
        let stream = AsyncStream<Void> { continuation in
            for source in sources { source.setEventHandler { continuation.yield(()) }; source.resume() }
        }
        for await _ in stream { break }
        for source in sources { source.cancel() }
        await stop()
    }
}

/// A small connection budget also bounds incomplete bodies and slow readers.
private final class DesktopHTTPConnections: @unchecked Sendable {
    private let lock = NSLock()
    private var channels: [ObjectIdentifier: Channel] = [:]
    func admit(_ channel: Channel) -> Bool {
        lock.lock()
        guard channels.count < 32 else { lock.unlock(); return false }
        let id = ObjectIdentifier(channel)
        channels[id] = channel
        lock.unlock()
        channel.closeFuture.whenComplete { [self] _ in
            lock.lock(); channels.removeValue(forKey: id); lock.unlock()
        }
        return true
    }
    func all() -> [Channel] {
        lock.lock(); defer { lock.unlock() }
        return Array(channels.values)
    }
}

/// One bounded request per TCP connection. HTTP sessions can span many connections.
private final class DesktopHTTPHandler: ChannelInboundHandler, @unchecked Sendable {
    typealias InboundIn = HTTPServerRequestPart
    typealias OutboundOut = HTTPServerResponsePart
    private let policy: DesktopHTTPPolicy
    private let sessions: DesktopHTTPSessions
    private var head: HTTPRequestHead?
    private var headers: [String: String] = [:]
    private var body = Data()
    private var responding = false
    private var deadline: Scheduled<Void>?
    private var task: Task<Void, Never>?

    init(policy: DesktopHTTPPolicy, sessions: DesktopHTTPSessions) {
        self.policy = policy
        self.sessions = sessions
    }

    func channelActive(context: ChannelHandlerContext) {
        // An incomplete header/body cannot keep a slot forever.
        deadline = context.eventLoop.scheduleTask(in: .seconds(15)) { context.close(promise: nil) }
        context.fireChannelActive()
    }

    func channelRead(context: ChannelHandlerContext, data: NIOAny) {
        guard !responding else { return }
        switch unwrapInboundIn(data) {
        case .head(let incoming):
            head = incoming
            for (name, value) in incoming.headers {
                let name = name.lowercased()
                guard headers[name] == nil else { reject(400, context); return }
                headers[name] = value
            }
            if let rejection = policy.rejection(request()) { send(rejection, context: context); return }
            if let length = headers["content-length"], (Int(length) ?? Int.max) > DesktopHTTPPolicy.bodyLimit {
                reject(413, context); return
            }
        case .body(let buffer):
            guard body.count + buffer.readableBytes <= DesktopHTTPPolicy.bodyLimit else { reject(413, context); return }
            body.append(contentsOf: buffer.readableBytesView)
        case .end(let trailers):
            guard head != nil, trailers == nil else { reject(400, context); return }
            let request = request()
            responding = true
            deadline?.cancel()
            deadline = context.eventLoop.scheduleTask(in: .seconds(20)) { context.close(promise: nil) }
            nonisolated(unsafe) let context = context
            task = Task {
                let reply = await sessions.handle(request)
                await write(reply.response, context: context, lease: reply.lease)
                await sessions.finish(reply.lease)
            }
        }
    }

    private func request() -> HTTPRequest {
        HTTPRequest(method: head?.method.rawValue ?? "", headers: headers, body: body, path: head?.uri)
    }

    private func reject(_ status: Int, _ context: ChannelHandlerContext) {
        send(DesktopHTTPPolicy.error(status, "Invalid or oversized HTTP request"), context: context)
    }

    private func send(_ response: HTTPResponse, context: ChannelHandlerContext) {
        responding = true
        nonisolated(unsafe) let context = context
        task = Task { await write(response, context: context) }
    }

    private func write(_ response: HTTPResponse, context: ChannelHandlerContext, lease: String? = nil) async {
        nonisolated(unsafe) let context = context
        do {
            try await context.eventLoop.submit {
                var head = HTTPResponseHead(version: .http1_1, status: .init(statusCode: response.statusCode))
                for (name, value) in response.headers { head.headers.replaceOrAdd(name: name, value: value) }
                head.headers.replaceOrAdd(name: "Connection", value: "close")
                context.writeAndFlush(self.wrapOutboundOut(.head(head)), promise: nil)
            }.get()
            if case .stream(let stream, _) = response {
                for try await chunk in stream {
                    try Task.checkCancellation()
                    try await writeBody(chunk, context: context)
                }
            } else if let data = response.bodyData {
                try await writeBody(data, context: context)
            }
            // Release before the final HTTP bytes so the next client request cannot race this lease.
            await sessions.finish(lease)
            try await context.eventLoop.flatSubmit {
                context.writeAndFlush(self.wrapOutboundOut(.end(nil)))
            }.get()
        } catch { /* A closed socket does not cancel or replay the durable handoff. */ }
        context.eventLoop.execute { context.close(promise: nil) }
    }

    private func writeBody(_ data: Data, context: ChannelHandlerContext) async throws {
        nonisolated(unsafe) let context = context
        try await context.eventLoop.flatSubmit {
            var buffer = context.channel.allocator.buffer(capacity: data.count)
            buffer.writeBytes(data)
            return context.writeAndFlush(self.wrapOutboundOut(.body(.byteBuffer(buffer))))
        }.get()
    }

    func channelInactive(context: ChannelHandlerContext) {
        deadline?.cancel()
        task?.cancel()
        context.fireChannelInactive()
    }

    func errorCaught(context: ChannelHandlerContext, error: Error) {
        context.close(promise: nil)
    }
}
