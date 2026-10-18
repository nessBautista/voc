"""Compose factual Spanish answers from saved evidence and closed visual descriptors."""

from .contracts import PLATFORMS, VIEWS, validate_result


def operations(plan):
    if plan.intent in ("unsupported", "clarify"):
        return []
    if plan.intent in ("freshness", "embeddings"):
        return ["freshness", "embeddings"]
    return [plan.intent]


def compose(plan, results, publication_id, trace, usage):
    """Keep numbers and dates in templates; the model only selects an intent."""
    by_kind = {result["kind"]: result for result in results}
    views = []
    if plan.intent == "unsupported":
        text = "Esta versión solo consulta el estado de los datos publicados: resumen, fuentes, cobertura, fechas de recolección y embeddings. No ofrece ese análisis ni ejecuta pipelines o experimentos. No apliqué filtros adicionales."
    elif plan.intent == "clarify":
        text = "¿Quieres conocer las fuentes, el total de registros, la cobertura mensual, las fechas de recolección o el estado de los embeddings? Puedes consultar una plataforma en fuentes o recolección; la cobertura mensual corresponde al dataset completo."
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
    if [result["kind"] for result in results] != operations(plan):
        raise ValueError("Unexpected query evidence")
    for result in results:
        validate_result(result, publication_id, result["kind"])
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
