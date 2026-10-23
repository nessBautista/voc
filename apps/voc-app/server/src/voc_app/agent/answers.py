"""Compose factual Spanish answers from saved evidence and closed visual descriptors."""

from .contracts import ANALYSIS_INTENTS, PLATFORMS, VIEWS, validate_result

DEFAULT_LIMIT = {"main_complaints": 3, "specific_problems": 5, "focus": 5}
# A topic needs this many classified reviews before a ranking names it.
FEATURE_MINIMUM = 5
SPECIFIC_MINIMUM = 100
REVIEW_PAGE = 6
INTENT_VIEWS = {
    "mood": ["mood_ring", "platforms"],
    "main_complaints": ["topic_list", "kpi_tiles"],
    "trend": ["timeline"],
    "topic_reviews": ["topic_detail"],
    "specific_problems": ["topic_list"],
    "focus": ["topic_list", "kpi_tiles"],
}
LABEL_WORDS = {"negative": "negativas", "neutral": "neutras", "positive": "positivas"}
MONTH_NAMES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def operations(plan):
    """The kinds an intent runs, each with the selection it sends, in order."""
    if plan.intent in ("unsupported", "clarify"):
        return []
    if plan.intent in ("freshness", "embeddings"):
        return [("freshness", None), ("embeddings", None)]
    if plan.intent not in ANALYSIS_INTENTS:
        return [(plan.intent, None)]
    base = {} if plan.selection is None else plan.selection.filters()
    if plan.intent == "mood":
        return [("summary", base), ("platforms", base)]
    if plan.intent == "trend":
        return [("timeline", base)]
    if plan.intent == "topic_reviews":
        if plan.topic.topic_id is None:
            raise ValueError("Unresolved topic reference")
        topic = {**base, "topic_id": plan.topic.topic_id}
        return [
            ("topic_detail", topic),
            ("reviews", {**topic, "page": 1, "page_size": REVIEW_PAGE}),
        ]
    return [("summary", base), ("topic_list", {**base, "sort": "negative"})]


def feature_ranking(rows, minimum=FEATURE_MINIMUM, single_only=False):
    """Feature topics ranked as the dashboard's tile ranks them: by the Wilson bound."""
    assessments = ("single_feature",) if single_only else ("single_feature", "mixed")
    eligible = [
        r
        for r in rows
        if r["topic_id"] >= 0
        and r["assessment"] in assessments
        and r["classified"] >= minimum
    ]
    eligible.sort(key=lambda r: (-r["wilson_lower"], -r["classified"], r["topic_id"]))
    return eligible


def named_topics(plan, results):
    """The topics an answer names, in order; the same list the memory keeps."""
    by_kind = {result["kind"]: result for result in results}
    if plan.intent not in DEFAULT_LIMIT or "topic_list" not in by_kind:
        return []
    rows = by_kind["topic_list"]["data"]
    if plan.intent == "specific_problems":
        tile = by_kind.get("summary", {}).get("data", {}).get("most_negative_feature")
        ranked = [
            r
            for r in feature_ranking(rows, SPECIFIC_MINIMUM, single_only=True)
            if tile is None or r["topic_id"] != tile["topic_id"]
        ]
    else:
        ranked = feature_ranking(rows)
    limit = plan.limit or DEFAULT_LIMIT[plan.intent]
    return [
        {"topic_id": r["topic_id"], "display_name": r["display_name"]}
        for r in ranked[:limit]
    ]


def opened_topic(plan, results):
    """The topic whose reviews an answer showed, or None."""
    if plan.intent != "topic_reviews":
        return None
    detail = next((r for r in results if r["kind"] == "topic_detail"), None)
    if detail is None:
        return None
    return {
        "topic_id": detail["data"]["topic_id"],
        "display_name": detail["data"]["display_name"],
    }


def month_words(month):
    """'2026-05' reads as 'mayo de 2026'."""
    return f"{MONTH_NAMES[int(month[5:]) - 1]} de {month[:4]}"


def join_words(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]


def scope(selection):
    """The selection in words, the way the dashboard's filter bar would read it."""
    platforms = selection.platforms if selection else None
    parts = [
        "en todas las plataformas"
        if not platforms
        else "en " + join_words(PLATFORMS[p] for p in sorted(platforms))
    ]
    month_from = selection.month_from if selection else None
    month_to = selection.month_to if selection else None
    if month_from and month_to:
        parts.append(f"entre {month_words(month_from)} y {month_words(month_to)}")
    elif month_from:
        parts.append(f"desde {month_words(month_from)}")
    elif month_to:
        parts.append(f"hasta {month_words(month_to)}")
    else:
        parts.append("en todo el periodo publicado")
    if selection and selection.labels:
        parts.append(
            "solo reseñas "
            + join_words(LABEL_WORDS[label] for label in selection.labels)
        )
    if selection and selection.search:
        parts.append(f"que mencionan «{selection.search}»")
    return ", ".join(parts)


def compose(plan, results, publication_id, trace, usage):
    """Keep numbers and dates in templates; the model only selects an intent."""
    by_kind = {result["kind"]: result for result in results}
    views = []
    if plan.intent == "unsupported":
        text = "Esta versión consulta el estado de los datos publicados y su análisis: ánimo general, quejas principales, evolución mensual, reseñas de un tema, problemas específicos y prioridades. No ofrece ese análisis ni ejecuta pipelines o experimentos. No apliqué filtros adicionales."
    elif plan.intent == "clarify":
        if plan.topic is not None:
            text = "¿A qué tema te refieres? Pregunta primero por la queja principal, o indica el número del tema."
        else:
            text = "¿Quieres conocer el ánimo general, la queja principal, su evolución mensual, las reseñas de un tema, los problemas más específicos o dónde poner atención primero? También puedo describir las fuentes, el total de registros, la cobertura mensual, las fechas de recolección o el estado de los embeddings."
    elif plan.intent in ANALYSIS_INTENTS:
        where = scope(plan.selection)
        where = where[0].upper() + where[1:]
        if plan.intent == "trend":
            data = by_kind["timeline"]["data"]
            months = data["months"]
            text = (
                f"{where}, la serie cubre {len(months)} meses, de {month_words(months[0]['month'])} a {month_words(months[-1]['month'])}, con {data['reviews_in_view']:,} reseñas."
                if months
                else f"{where}, no hay meses con reseñas fechadas."
            )
        elif plan.intent == "topic_reviews":
            data = by_kind["topic_detail"]["data"]
            text = f"Tema {data['topic_id']}, «{data['display_name']}»: {data['in_view']:,} de {data['member_count']:,} reseñas cumplen los filtros ({where[0].lower() + where[1:]})."
        else:
            data = by_kind["summary"]["data"]
            text = f"{where}, hay {data['reviews_in_view']:,} reseñas en vista de {data['reviews_total']:,} publicadas; {data['classified']:,} tienen etiqueta de sentimiento."
        views = [
            {
                "component": component,
                "result_ids": [by_kind[kind]["result_id"] for kind in VIEWS[component]],
            }
            for component in INTENT_VIEWS[plan.intent]
        ]
    else:
        data = by_kind[plan.intent]["data"]
        if plan.intent == "overview":
            text = f"La publicación consultada contiene {data['row_count']:,} registros; {data['unknown_dates']:,} no tienen una fecha conocida."
            component = "dataset_summary"
        elif plan.intent == "sources":
            if plan.platform:
                row = next(
                    (row for row in data if row["platform"] == plan.platform), None
                )
                text = (
                    f"La publicación consultada contiene {row['record_count']:,} registros de {PLATFORMS[plan.platform]}."
                    if row
                    else f"La publicación no contiene registros de {PLATFORMS[plan.platform]}."
                )
            else:
                text = (
                    "Fuentes de la publicación: "
                    + (
                        "; ".join(
                            f"{PLATFORMS.get(row['platform'], row['platform'])}: {row['record_count']:,} registros"
                            for row in data
                        )
                        or "ninguna fuente con registros"
                    )
                    + "."
                )
            component = "sources"
        elif plan.intent == "coverage":
            months = data["months"]
            text = (
                f"Hay registros en {len(months)} meses, desde {months[0]['month']} hasta {months[-1]['month']}."
                if months
                else "No hay meses con registros fechados."
            )
            text += f" Hay {data['unknown_dates']:,} registros sin fecha. Los meses ausentes no se completan con ceros; esto no demuestra que la recolección esté completa."
            component = "coverage"
        elif plan.intent == "freshness":
            rows = [
                row
                for row in data["sources"]
                if not plan.platform or row["platform"] == plan.platform
            ]
            descriptions = [
                f"{PLATFORMS.get(row['platform'], row['platform'])}: "
                + (
                    f"recolección documentada el {row['latest_collection_at']}"
                    if row["collection_state"] == "verified"
                    else "fecha de recolección desconocida"
                )
                for row in rows
            ]
            text = (
                "; ".join(descriptions) + "."
                if descriptions
                else "No hay una fuente registrada para esa selección."
            )
            text += f" Preparación del dataset: {data['prepared_at'] or 'no documentada'}. Creación del paquete: {data['publication_created_at']}. La creación del paquete no prueba que se hayan obtenido datos nuevos."
            component = "preparation"
        else:
            if data["state"] == "not_reported":
                text = "La publicación no informa evidencia sobre embeddings. Esto no significa que no existan."
            else:
                state = (
                    "verificados"
                    if data["state"] == "verified"
                    else "registrados, sin verificación documentada"
                )
                text = f"Embeddings {state}: modelo {data['model']}, cobertura de {data['covered_rows']:,} registros."
            component = "preparation"
        views = [
            {
                "component": component,
                "result_ids": [by_kind[kind]["result_id"] for kind in VIEWS[component]],
            }
        ]
    warnings = list(
        {
            (item["code"], item["message"]): item
            for result in results
            for item in result["warnings"]
        }.values()
    )
    return {
        "schema_version": "voc-answer-v1",
        "publication_id": publication_id,
        "plan": plan.model_dump(),
        "text": text,
        "results": results,
        "views": views,
        "warnings": warnings,
        "trace": trace,
        "usage": usage,
    }


def validate_answer(answer, plan, publication_id):
    """Validate evidence first, then require the exact deterministic composition."""
    results = answer["results"]
    expected = operations(plan)
    if [result["kind"] for result in results] != [kind for kind, _ in expected]:
        raise ValueError("Unexpected query evidence")
    for result, (kind, selection) in zip(results, expected, strict=True):
        validate_result(result, publication_id, kind, selection)
    if answer["publication_id"] != publication_id or len(answer["trace"]) != len(
        results
    ):
        raise ValueError("Answer identity or trace mismatch")
    for entry, result in zip(answer["trace"], results, strict=True):
        if (
            any(entry[key] != result[key] for key in ("publication_id", "result_id"))
            or entry["operation"] != result["kind"]
        ):
            raise ValueError("Trace does not match evidence")
    expected = compose(plan, results, publication_id, answer["trace"], answer["usage"])
    if answer != expected:
        raise ValueError("Answer or visual reference differs from verified composition")
    return answer
