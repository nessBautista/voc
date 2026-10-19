import Foundation

/// A handoff starts one server conversation; its opening state is not the answer state.
struct HandoffWork: Codable, Equatable {
    enum State: String, Codable { case opening, opened, failed, expired, abandoned }

    let workID: String
    let requestID: String
    let question: String
    let serverOrigin: String
    let requestBody: String
    let createdAt: Double
    let expiresAt: Double
    var updatedAt: Double
    var state: State = .opening
    var active = true
    var claimedBy: String?
    var conversationID: String?
    var publicationID: String?
    var turnStatus: String?
    var errorCode: String?
    // Optional fields preserve records created before local layout existed.
    var presentation: HandoffPresentation?
    var layoutAttempted: Bool?
    var layoutResult: HandoffLayoutResult?
    var reports: [HandoffReport]?
    var reportRequestID: String?

    var link: URL { URL(string: "voc-data-state://handoff/\(workID)")! }

    static func identity(from url: URL) throws -> String {
        // Deep links select existing work. They never carry a question or a server.
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "voc-data-state", parts.host == "handoff",
              parts.user == nil, parts.password == nil, parts.port == nil,
              parts.query == nil, parts.fragment == nil,
              parts.percentEncodedPath == parts.path,
              parts.path.count == 37
        else { throw HandoffError("invalid_link") }
        return try HandoffInput.identity(String(parts.path.dropFirst()))
    }
}

struct HandoffInput {
    let requestID: String
    let question: String
    let body: String

    init(requestID: String, question: String) throws {
        self.requestID = try Self.identity(requestID)
        // Python's Ask limit counts Unicode code points before stripping whitespace.
        guard (1...1000).contains(question.unicodeScalars.count) else {
            throw HandoffError("invalid_question")
        }
        let whitespace = CharacterSet.whitespacesAndNewlines
            .union(CharacterSet(charactersIn: "\u{001c}\u{001d}\u{001e}\u{001f}\u{0085}"))
            .subtracting(CharacterSet(charactersIn: "\u{200b}"))
        self.question = question.trimmingCharacters(in: whitespace)
        guard !self.question.isEmpty else { throw HandoffError("invalid_question") }
        let payload: [String: Any] = [
            "request_id": self.requestID,
            "question": self.question,
            "conversation_id": NSNull(),
        ]
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        body = String(decoding: data, as: UTF8.self)
    }

    static func identity(_ value: String) throws -> String {
        guard UUID(uuidString: value)?.uuidString.lowercased() == value else {
            throw HandoffError("invalid_id")
        }
        return value
    }
}

struct HandoffError: LocalizedError {
    let code: String
    init(_ code: String) { self.code = code }

    var errorDescription: String? {
        switch code {
        case "report_not_ready": return "Espera a que termine la pregunta y pulsa Preparar resultado para el agente."
        case "report_not_prepared": return "El usuario todavía no preparó un resultado. Continúa en VOC; no hace falta sondear."
        case "report_not_found", "report_not_associated": return "Ese informe no pertenece a este trabajo o ya no está disponible."
        case "invalid_report", "report_integrity_error", "report_publication_mismatch": return "No se pudo verificar la identidad o los bytes del informe. No se guardó un resultado incorrecto."
        case "invalid_report_offset": return "La posición de lectura debe estar dentro del informe preparado."
        case "report_too_large": return "El informe supera el límite de 8 MiB. No se publicó un archivo parcial."
        case "report_request_conflict": return "Esa petición ya corresponde a otra exportación."
        case "report_transport_failed": return "No se pudo recuperar el informe del servidor. Reintenta la misma operación cuando esté disponible."

        case "http_token_required": return "Configura VOC_DESKTOP_MCP_TOKEN con una credencial privada de 32 a 512 caracteres ASCII sin espacios."
        case "http_bind_failed": return "No se pudo escuchar en 127.0.0.1:8766. Comprueba si el puerto está ocupado; no se detuvo ningún proceso."
        case "invalid_id", "invalid_link": return "La entrega necesita un identificador UUID válido y un enlace sin parámetros."
        case "invalid_question": return "Escribe una pregunta de entre 1 y 1.000 caracteres."
        case "request_conflict": return "Ese identificador ya pertenece a otra pregunta."
        case "busy": return "Hay un trabajo activo. Termínalo o abandónalo desde VOC antes de iniciar otro."
        case "not_found": return "No existe esa entrega en este Mac."
        case "expired": return "La entrega venció antes de que VOC la recibiera. Crea una nueva petición."
        case "abandoned": return "El trabajo fue abandonado; su conversación se conserva en el servidor."
        case "origin_changed": return "El servidor cambió. Conecta al origen guardado para recuperar esta entrega."
        case "desktop_busy": return "Otra instancia de VOC está atendiendo las entregas de este Mac."
        case "invalid_response": return "El servidor no confirmó la identidad de esta pregunta. Puedes recuperar la entrega."
        case "transport_failed": return "No se confirmó el envío. Recuperar repite la misma petición, sin crear otro turno."
        case "launch_failed": return "No se pudo abrir VOC. Abre la aplicación y recupera el trabajo."
        case "navigation_failed": return "No se pudo mostrar la conversación. Puedes recuperar la entrega sin reenviar la pregunta."
        case "not_configured": return "Abre VOC y conecta al servidor antes de enviar una entrega."
        case "invalid_application": return "Ejecuta el helper incluido dentro de VOCDataState.app."
        case "inactive": return "Esta entrega ya no está activa."
        default: return "No se pudo completar la entrega (\(code))."
        }
    }
}
