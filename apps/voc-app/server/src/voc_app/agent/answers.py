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


def ranked_rows(plan, results):
    """The topic rows a ranking answer names, in order, capped by the plan's limit."""
    by_kind = {result["kind"]: result for result in results}
    if plan.intent not in DEFAULT_LIMIT or "topic_list" not in by_kind:
        return []
    rows = by_kind["topic_list"]["data"]
    if plan.intent == "specific_problems":
        # Volume, not the bound: which single features concentrate the most complaints.
        tile = by_kind.get("summary", {}).get("data", {}).get("most_negative_feature")
        ranked = [
            r
            for r in feature_ranking(rows, SPECIFIC_MINIMUM, single_only=True)
            if tile is None or r["topic_id"] != tile["topic_id"]
        ]
        ranked.sort(
            key=lambda r: (-r["negative"], -(r["negative_share"] or 0), r["topic_id"])
        )
    else:
        ranked = feature_ranking(rows)
    return ranked[: plan.limit or DEFAULT_LIMIT[plan.intent]]


def named_topics(plan, results):
    """The topics an answer names, in order; the same list the memory keeps."""
    return [
        {"topic_id": r["topic_id"], "display_name": r["display_name"]}
        for r in ranked_rows(plan, results)
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


TREND_MINIMUM = 10
RISE_POINTS = 0.01
BOUND_SENTENCE = (
    "El orden usa el límite inferior de Wilson al 95% de la proporción negativa, que "
    "descuenta a los temas pequeños: un tema con pocas reseñas y todas negativas no "
    "supera a uno grande con casi todas negativas."
)


def pct(share):
    """A share as the tiles write it: whole percent, half rounded up, dash when unknown."""
    return "—" if share is None else f"{int(share * 100 + 0.5)}%"


def points(delta):
    return f"{int(abs(delta) * 100 + 0.5)} puntos"


def platform_name(platform):
    return PLATFORMS.get(platform, platform)


def counted(number, word):
    """'1 negativa', '3 negativas': the count with its noun in number."""
    return f"{number:,} {word}" + ("" if number == 1 else "s")


def three_parts(data_part, interpretation, limits):
    """Every analysis answer: what the data says, what is interpretation, what cannot be claimed."""
    return "\n\n".join(
        (
            "Qué dicen los datos. " + data_part,
            "Interpretación. " + interpretation,
            "Lo que no se puede afirmar. " + limits,
        )
    )


def rises(months):
    """Months whose negative share rose at least one point over a comparable month."""
    found, previous = [], None
    for month in months:
        share = (
            month["negative"] / month["classified"]
            if month["classified"] >= TREND_MINIMUM
            else None
        )
        if (
            share is not None
            and previous is not None
            and share - previous >= RISE_POINTS - 1e-9
        ):
            found.append(
                {"month": month["month"], "share": share, "rise": share - previous}
            )
        previous = share
    return found


def ranking_items(rows):
    # The topic number travels with the name: generated names repeat across topics.
    return "; ".join(
        f"{number}) «{row['display_name']}» (tema {row['topic_id']}): {row['negative']:,} de "
        f"{row['classified']:,} reseñas clasificadas negativas ({pct(row['negative_share'])}; "
        f"al menos {pct(row['wilson_lower'])})"
        for number, row in enumerate(rows, 1)
    )


def outside_ranking(rows):
    """Topics in view the feature ranking leaves out, and the unassigned reviews."""
    eligible = {r["topic_id"] for r in feature_ranking(rows)}
    left_out = sum(
        1 for r in rows if r["topic_id"] >= 0 and r["topic_id"] not in eligible
    )
    unassigned = sum(r["total"] for r in rows if r["topic_id"] == -1)
    return left_out, unassigned


def scope_sentence(plan, summary):
    where = scope(plan.selection)
    where = where[0].upper() + where[1:]
    return (
        f"{where}, hay {summary['reviews_in_view']:,} reseñas en vista de "
        f"{summary['reviews_total']:,} publicadas; {summary['classified']:,} tienen "
        "etiqueta de sentimiento."
    )


def mood_text(plan, by_kind):
    summary = by_kind["summary"]["data"]
    platforms = by_kind["platforms"]["data"]
    data_part = scope_sentence(plan, summary)
    classified = summary["classified"]
    unclassified = summary["reviews_in_view"] - classified
    if classified == 0:
        data_part += " No hay reseñas con etiqueta de sentimiento en esta selección."
        interpretation = "Sin etiquetas no hay un ánimo que describir."
    else:
        negative, neutral, positive = (
            summary[label] for label in ("negative", "neutral", "positive")
        )
        data_part += (
            f" De ellas, {pct(negative['share'])} son negativas ({negative['count']:,}), "
            f"{pct(neutral['share'])} neutras ({neutral['count']:,}) y "
            f"{pct(positive['share'])} positivas ({positive['count']:,})."
        )
        rated = [row for row in platforms if row["classified"] > 0]
        if len(rated) >= 2:
            high = max(rated, key=lambda r: (r["negative_share"], r["classified"]))
            low = min(rated, key=lambda r: (r["negative_share"], -r["classified"]))
            data_part += (
                f" Por plataforma, la mayor proporción negativa está en {platform_name(high['platform'])} "
                f"({pct(high['negative_share'])} de {high['classified']:,} clasificadas) y la menor en "
                f"{platform_name(low['platform'])} ({pct(low['negative_share'])} de {low['classified']:,})."
            )
        elif len(rated) == 1:
            data_part += f" Solo {platform_name(rated[0]['platform'])} tiene reseñas clasificadas en esta selección."
        difference = positive["share"] - negative["share"]
        if difference >= 0.10:
            interpretation = f"El ánimo general es predominantemente positivo: las positivas superan a las negativas por {points(difference)}."
        elif difference <= -0.10:
            interpretation = f"El ánimo general es predominantemente negativo: las negativas superan a las positivas por {points(difference)}."
        else:
            interpretation = f"El ánimo general está dividido: positivas y negativas difieren en {points(difference)}, menos de diez."
    limits = (
        f"{unclassified:,} reseñas en vista no tienen etiqueta y no entran en las proporciones. "
        "Las etiquetas de sentimiento son resultados de un modelo, no juicios de personas. "
        "Una proporción cuenta reseñas, no personas: una misma persona puede dejar varias."
    )
    return three_parts(data_part, interpretation, limits)


def main_complaints_text(plan, by_kind, results):
    summary = by_kind["summary"]["data"]
    rows = by_kind["topic_list"]["data"]
    named = ranked_rows(plan, results)
    data_part = scope_sentence(plan, summary)
    left_out, unassigned = outside_ranking(rows)
    if not named:
        data_part += f" No hay temas de una funcionalidad con al menos {FEATURE_MINIMUM} reseñas clasificadas en esta selección."
        interpretation = BOUND_SENTENCE
    else:
        first = named[0]
        data_part += (
            f" El tema con mayor proporción negativa entre los de una funcionalidad es el tema "
            f"{first['topic_id']}, «{first['display_name']}»: {first['negative']:,} de "
            f"{first['classified']:,} reseñas clasificadas son negativas "
            f"({pct(first['negative_share'])}; al menos {pct(first['wilson_lower'])} con 95% de confianza)."
        )
        if len(named) > 1:
            data_part += (
                " Le siguen "
                + join_words(
                    f"«{row['display_name']}» (tema {row['topic_id']}, {pct(row['negative_share'])}, {counted(row['negative'], 'negativa')})"
                    for row in named[1:]
                )
                + "."
            )
        keywords = join_words(first["keywords"][:3])
        interpretation = (
            "Los nombres de los temas son generados por un modelo a partir de sus reseñas"
            + (
                f"; «{first['display_name']}» reúne reseñas con palabras clave como {keywords}."
                if keywords
                else "."
            )
            + " "
            + BOUND_SENTENCE
        )
    limits = (
        f"{left_out} temas en vista no tienen un nombre generado, no describen una funcionalidad "
        f"o tienen menos de {FEATURE_MINIMUM} reseñas clasificadas, y no participan en este orden. "
        f"{unassigned:,} reseñas en vista no están asignadas a ningún tema. "
        "Una proporción alta de negativas no mide cuántas personas sufren el problema."
    )
    return three_parts(data_part, interpretation, limits)


def trend_text(plan, by_kind):
    data = by_kind["timeline"]["data"]
    months = data["months"]
    where = scope(plan.selection)
    where = where[0].upper() + where[1:]
    if not months:
        return three_parts(
            f"{where}, no hay meses con reseñas fechadas.",
            "Sin meses no hay una tendencia que leer.",
            "Las reseñas sin fecha no entran en la serie.",
        )
    last = months[-1]["month"]
    data_part = (
        f"{where}, la serie cubre {len(months)} meses, de {month_words(months[0]['month'])} a "
        f"{month_words(last)}, con {data['reviews_in_view']:,} reseñas."
    )
    found = rises(months)
    thin = [m["month"] for m in months if m["classified"] < TREND_MINIMUM]
    if len(thin) == len(months):
        data_part += f" Ningún mes alcanza las {TREND_MINIMUM} reseñas clasificadas necesarias para comparar proporciones."
        interpretation = "Con tan pocas reseñas por mes la serie no permite hablar de una tendencia; mira el volumen de cada mes en el bloque."
    elif found:
        data_part += (
            " La proporción de negativas subió respecto al mes anterior en "
            + join_words(
                f"{month_words(item['month'])} (+{points(item['rise'])}, a {pct(item['share'])})"
                for item in found
            )
            + "."
        )
        biggest = max(found, key=lambda item: (item["rise"], item["month"]))
        interpretation = (
            f"El mayor aumento fue en {month_words(biggest['month'])}, de {points(biggest['rise'])}. "
            "Un aumento de la proporción negativa acompañado de más reseñas apunta a un incidente o a un "
            "cambio en la aplicación; un aumento con pocas reseñas puede ser ruido."
        )
    else:
        data_part += " La proporción de negativas no subió un punto o más en ningún mes respecto al anterior."
        interpretation = "Sin aumentos de un punto o más, la serie no señala un mes de quiebre; mira el volumen de cada mes en el bloque antes de concluir estabilidad."
    if data["busiest"]:
        data_part += f" El mes con más reseñas fue {month_words(data['busiest']['month'])}, con {data['busiest']['total']:,}."
    limits = (
        f"Los meses con menos de {TREND_MINIMUM} reseñas clasificadas no se comparan"
    )
    if thin and len(thin) < len(months):
        limits += (
            ": " + join_words(month_words(m) for m in thin)
            if len(thin) <= 6
            else f": {len(thin)} meses de la serie"
        )
    limits += (
        f". El último mes, {month_words(last)}, puede estar incompleto si la recolección terminó antes "
        "de fin de mes. La serie sigue la selección; no separa plataformas salvo que la selección lo haga."
    )
    return three_parts(data_part, interpretation, limits)


def topic_reviews_text(plan, by_kind):
    detail = by_kind["topic_detail"]["data"]
    reviews = by_kind["reviews"]["data"]
    where = scope(plan.selection)
    data_part = (
        f"Tema {detail['topic_id']}, «{detail['display_name']}»: {detail['in_view']:,} de "
        f"{detail['member_count']:,} reseñas cumplen los filtros ({where}); "
        f"{counted(detail['negative'], 'negativa')}, {counted(detail['neutral'], 'neutra')} y "
        f"{counted(detail['positive'], 'positiva')} ({pct(detail['negative_share'])} negativas)."
    )
    shown = len(reviews["items"])
    if shown:
        rated = sum(1 for item in reviews["items"] if item["rating"] is not None)
        # One label asked for: the sentiment order is moot, only confidence orders the page.
        order = (
            "ordenadas por confianza"
            if plan.selection
            and plan.selection.labels
            and len(plan.selection.labels) == 1
            else "de las negativas con mayor confianza a las positivas"
        )
        data_part += (
            f" Se muestran {shown} de {reviews['total']:,} reseñas, {order}; "
            f"{rated} traen calificación de la tienda."
        )
    else:
        data_part += " No hay reseñas que mostrar con estos filtros."
    keywords = join_words(detail["keywords"])
    if detail["wording_source"] == "generated" and detail["description"]:
        interpretation = (
            f"Descripción generada por un modelo: «{detail['description']}»"
            + (f" Palabras clave: {keywords}." if keywords else "")
        )
    elif keywords:
        interpretation = f"Este tema no tiene una descripción generada; sus palabras clave son {keywords}."
    else:
        interpretation = "Este grupo reúne reseñas sin tema asignado; no tiene descripción ni palabras clave."
    limits = (
        "La página muestra reseñas ordenadas por sentimiento y confianza, no las más representativas del tema. "
        "La asignación de una reseña al tema es un resultado de modelo. "
        + (
            f"{outside:,} reseñas del tema no entran en esta vista."
            if (outside := max(detail["member_count"] - detail["in_view"], 0))
            else "Todas las reseñas del tema entran en esta vista."
        )
    )
    return three_parts(data_part, interpretation, limits)


def specific_problems_text(plan, by_kind, results):
    summary = by_kind["summary"]["data"]
    named = ranked_rows(plan, results)
    tile = summary["most_negative_feature"]
    data_part = scope_sentence(plan, summary)
    lead = f" Más allá del tema principal, «{tile['display_name']}»," if tile else ""
    if named:
        data_part += (
            f"{lead} los problemas de una sola funcionalidad con más reseñas negativas, "
            f"entre los temas con al menos {SPECIFIC_MINIMUM} reseñas clasificadas, son: "
            f"{ranking_items(named)}."
        )
        negatives = sum(row["negative"] for row in named)
        share = (
            negatives / summary["negative"]["count"]
            if summary["negative"]["count"]
            else None
        )
        interpretation = (
            "Cada uno describe una funcionalidad concreta según la interpretación generada. "
            "Este orden va por volumen, no por proporción: señala dónde se concentran las "
            "quejas, mientras que la queja principal y las prioridades usan la proporción "
            f"negativa. Juntos suman {negatives:,} reseñas negativas, {pct(share)} de las "
            "negativas en vista."
        )
    else:
        data_part += (
            f"{lead} no hay otros temas de una sola funcionalidad con al menos "
            f"{SPECIFIC_MINIMUM} reseñas clasificadas en esta selección."
        )
        interpretation = "Con esta selección el análisis no separa problemas más específicos; amplía la selección o pregunta por la queja principal."
    limits = (
        f"Los temas con menos de {SPECIFIC_MINIMUM} reseñas clasificadas, los de interpretación mixta o "
        "poco clara y los no interpretados no aparecen aquí aunque sean muy negativos. "
        "Un tema puede mezclar varias causas."
    )
    return three_parts(data_part, interpretation, limits)


def focus_text(plan, by_kind, results):
    summary = by_kind["summary"]["data"]
    rows = by_kind["topic_list"]["data"]
    named = ranked_rows(plan, results)
    left_out, unassigned = outside_ranking(rows)
    data_part = scope_sentence(plan, summary)
    if named:
        data_part += (
            " Ordenados por el límite inferior de la proporción negativa, los temas de una "
            f"funcionalidad a atender primero son: {ranking_items(named)}."
        )
    else:
        data_part += f" No hay temas de una funcionalidad con al menos {FEATURE_MINIMUM} reseñas clasificadas en esta selección."
    interpretation = (
        BOUND_SENTENCE
        + " Los nombres son generados por un modelo y señalan la funcionalidad, no la causa."
    )
    limits = (
        "Este orden es una señal estadística, no una prioridad de negocio: no considera el costo de "
        f"corregir, el impacto en ingresos ni cuántas personas afecta. {unassigned:,} reseñas en vista no "
        f"tienen tema y {left_out} temas sin nombre generado, sin una funcionalidad clara o con menos de "
        f"{FEATURE_MINIMUM} reseñas clasificadas quedan fuera."
    )
    return three_parts(data_part, interpretation, limits)


def analysis_text(plan, by_kind, results):
    if plan.intent == "mood":
        return mood_text(plan, by_kind)
    if plan.intent == "main_complaints":
        return main_complaints_text(plan, by_kind, results)
    if plan.intent == "trend":
        return trend_text(plan, by_kind)
    if plan.intent == "topic_reviews":
        return topic_reviews_text(plan, by_kind)
    if plan.intent == "specific_problems":
        return specific_problems_text(plan, by_kind, results)
    return focus_text(plan, by_kind, results)


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
        text = analysis_text(plan, by_kind, results)
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
