from .models import DecisionFinal


def persist_final_decision(solicitud, result: dict, observaciones: str = ""):
    decision, _ = DecisionFinal.objects.update_or_create(
        solicitud=solicitud,
        defaults={
            "resultado": result["resultado"],
            "mensaje": result["mensaje"],
            "observaciones": observaciones,
            "monto_aprobado": result.get("monto_aprobado"),
            "plazo_aprobado": result.get("plazo_aprobado"),
            "tasa_interes": result.get("tasa_interes"),
            "detalle": result.get("detalle", {}),
        },
    )
    solicitud.estado = "FINALIZADA"
    solicitud.paso_actual = "resultado"
    solicitud.ultimo_error = ""
    solicitud.save(update_fields=("estado", "paso_actual", "ultimo_error", "updated_at"))
    return decision
