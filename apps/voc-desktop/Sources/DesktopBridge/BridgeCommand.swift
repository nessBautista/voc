import AppKit

/// Increment 11's local probe. MCP transports will call the same launcher later.
@main
struct BridgeCommand {
    @MainActor
    static func main() async {
        do {
            let arguments = Array(CommandLine.arguments.dropFirst())
            guard let verb = arguments.first, ["submit", "status"].contains(verb) else {
                throw HandoffError("usage_submit_or_status")
            }
            let store = try HandoffStore(directory: DesktopFiles.directory())
            let work: HandoffWork
            if verb == "submit" {
                guard arguments.count == 1 else { throw HandoffError("invalid_arguments") }
                // Only the two fields below are accepted; no caller-selected server or path.
                let data = FileHandle.standardInput.readData(ofLength: 8193)
                guard data.count <= 8192,
                      let payload = try JSONSerialization.jsonObject(with: data) as? [String: String],
                      Set(payload.keys) == ["request_id", "question"] else {
                    throw HandoffError("invalid_arguments")
                }
                let input = try HandoffInput(requestID: payload["request_id"]!, question: payload["question"]!)
                let application = try NativeHandoffOpening.bundledApplication(
                    executable: URL(fileURLWithPath: CommandLine.arguments[0])
                )
                _ = NSApplication.shared
                let launcher = HandoffLauncher(store: store, opener: NativeHandoffOpening(application: application))
                work = try await launcher.submit(input)
            } else {
                guard arguments.count == 2 else { throw HandoffError("invalid_arguments") }
                work = try store.read(arguments[1])
            }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            encoder.keyEncodingStrategy = .convertToSnakeCase
            FileHandle.standardOutput.write(try encoder.encode(work))
            FileHandle.standardOutput.write(Data("\n".utf8))
        } catch {
            let failure = error as? HandoffError ?? HandoffError("handoff_failed")
            let data = try! JSONSerialization.data(withJSONObject: ["error": failure.code, "message": failure.localizedDescription], options: [.sortedKeys])
            FileHandle.standardError.write(data + Data("\n".utf8))
            exit(1)
        }
    }
}
