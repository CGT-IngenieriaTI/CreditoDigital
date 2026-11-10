"""Preflight, autorizacion puntual y reporte local. Nunca consulta proveedores."""

from hashlib import sha256
import json
import re
from uuid import UUID

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from apps.decisiones.models import DecisionFinal
from apps.historial_pago.models import HistorialPagoConsulta
from apps.preselecta.models import PreselectaConsulta
from apps.solicitudes.models import Solicitud
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from ..credihoy_integracion.cliente import ConfiguracionApiData
from .asincrono import clave_funcional, encolar_shadow, TERMINALES
from .evidencia import disponibilidad_externa, identidad_valida, recuperar_externos
from .servicio import VERSION_POLITICA, _request_id

AUTORIZACION = "credihoy_shadow_piloto_autorizado"
CAMPOS_TAREA = ("solicitud_id", "request_id", "fecha_evaluacion", "evaluacion_actualizada", "version_politica")


def huella(valor):
    serializado = json.dumps(valor, cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":"))
    return sha256(serializado.encode("utf-8")).hexdigest()


def capturar_invariantes(solicitud_id):
    """Incluye todos los campos persistidos, sin copiar datos sensibles al AuditLog."""
    filas = {}
    for modelo in (Solicitud, SolicitudConsumo, DecisionFinal, EvaluacionConsumo):
        filtro = {"pk": solicitud_id} if modelo is Solicitud else {"solicitud_id": solicitud_id}
        filas[modelo.__name__] = modelo.objects.filter(**filtro).values().first()
    decision = filas["DecisionFinal"] or {}
    return {
        "hashes": {nombre: huella(fila) for nombre, fila in filas.items()},
        "estado_solicitud": (filas["Solicitud"] or {}).get("estado"),
        "decision_productiva": decision.get("resultado"),
        "monto_tasa_sha256": huella({k: decision.get(k) for k in ("monto_aprobado", "tasa_interes")}),
        # El pipeline devuelve bytes; no almacena el PDF de decision en un FileField.
        "pdf": {"sha256": None, "motivo": "PDF_DECISION_NO_PERSISTIDO",
                "pdf_generado": (filas["EvaluacionConsumo"] or {}).get("pdf_generado")},
    }


def preflight(solicitud_id, *, tipo_piloto="PILOTO_COMPARATIVO"):
    errores = []
    if tipo_piloto not in {"PILOTO_COMPARATIVO", "PILOTO_TECNICO"}:
        return {"estado": "PILOTO_NO_APTO", "errores": ["TIPO_PILOTO_INVALIDO"]}
    tecnico = tipo_piloto == "PILOTO_TECNICO"
    if tecnico:
        from .importador import exigir_local
        try:
            exigir_local()
        except ValueError:
            return {"estado": "PILOTO_NO_APTO", "errores": ["REQUIERE_DEBUG_Y_SQLITE_LOCAL"]}
    try:
        solicitud_id = str(UUID(str(solicitud_id)))
    except (ValueError, TypeError, AttributeError):
        return {"estado": "PILOTO_NO_APTO", "errores": ["SOLICITUD_ID_INVALIDO"]}
    if settings.CREDIHOY_SHADOW_ENABLED:
        errores.append("FLAG_GLOBAL_DEBE_SER_FALSE")
    try:
        ConfiguracionApiData.desde_settings()
    except (ValueError, TypeError):
        errores.append("CONFIGURACION_API_DATA_INCOMPLETA")
    solicitud = Solicitud.objects.select_related("solicitante").filter(pk=solicitud_id).first()
    if solicitud is None:
        return {"estado": "PILOTO_NO_APTO", "errores": [*errores, "SOLICITUD_NO_EXISTE"]}
    if str(solicitud.producto).upper() != "CONSUMO":
        errores.append("PRODUCTO_NO_SOPORTADO")
    if not SolicitudConsumo.objects.filter(solicitud=solicitud).exists():
        errores.append("SOLICITUD_CONSUMO_AUSENTE")
    decision_existe = DecisionFinal.objects.filter(solicitud=solicitud).exists()
    if not tecnico and not decision_existe:
        errores.append("DECISION_FINAL_AUSENTE")
    if tecnico:
        if solicitud.metadatos.get("shadow_import") != "credihoy-shadow-import-v1":
            errores.append("PILOTO_TECNICO_REQUIERE_IMPORTACION_LOCAL")
        if decision_existe:
            errores.append("UTILIZAR_PILOTO_COMPARATIVO_CON_DECISION_REAL")
    persona = solicitud.solicitante
    if not re.fullmatch(r"[0-9]{1,30}", persona.numero_identificacion or ""):
        errores.append("DOCUMENTO_NO_DISPONIBLE_O_INVALIDO")
    if not (persona.primer_apellido or "").strip() or not persona.tipo_identificacion:
        errores.append("IDENTIDAD_SOLICITANTE_INCOMPLETA")
    evaluacion = EvaluacionConsumo.objects.filter(solicitud=solicitud).first()
    pre = PreselectaConsulta.objects.filter(solicitud=solicitud).first()
    hdc = HistorialPagoConsulta.objects.filter(solicitud=solicitud).first()
    disponibles = {"PRESELECTA": False, "HDC": False}
    if evaluacion is None:
        errores.append("EVALUACION_CONSUMO_AUSENTE")
    else:
        snapshot = evaluacion.integraciones_snapshot
        if not isinstance(snapshot, dict):
            errores.append("SNAPSHOT_INVALIDO")
        else:
            previo = snapshot.get("preselecta_snapshot") or {}
            identidades = {
                "PRESELECTA": [previo.get("request_payload") if isinstance(previo, dict) else None,
                               pre.request_payload if pre else None],
                "HDC": [snapshot.get("historial_payload"), hdc.request_payload if hdc else None],
            }
            for fuente, candidatas in identidades.items():
                if any(p and not identidad_valida(p, persona) for p in candidatas):
                    errores.append(f"IDENTIDAD_{fuente}_INCONSISTENTE")
            try:
                externos, fallos = recuperar_externos(solicitud, evaluacion, preselecta=pre, historial=hdc)
                disponibles = disponibilidad_externa(externos)
                errores.extend(fallos)
            except (TypeError, ValueError, AttributeError):
                errores.append("EVIDENCIA_PERSISTIDA_INVALIDA")
    errores.extend(f"{k}_NO_IDENTIFICABLE" for k, disponible in disponibles.items() if not disponible)
    resultado = {"estado": "PILOTO_NO_APTO" if errores else "PILOTO_APTO",
                 "solicitud_id": solicitud_id, "errores": errores, "evidencia_disponible": disponibles,
                 "tipo_piloto": tipo_piloto}
    if not errores:
        version = evaluacion.updated_at
        resultado["datos_tarea"] = dict(solicitud_id=solicitud_id, version_politica=VERSION_POLITICA,
            evaluacion_actualizada=version.isoformat(),
            fecha_evaluacion=timezone.localtime(version).replace(microsecond=0).isoformat())
    return resultado


def autorizacion_valida(datos):
    """Un booleano en el broker no concede permiso: se exige evidencia local persistida."""
    try:
        piloto_id = int(datos.get("piloto_id", ""))
    except (ValueError, TypeError):
        return None
    evento = AuditLog.objects.filter(pk=piloto_id, event=AUTORIZACION,
                                    solicitud_id=datos["solicitud_id"]).first()
    if evento is None or settings.CREDIHOY_SHADOW_ENABLED:
        return None
    payload = evento.payload
    tipo = datos.get("tipo_piloto", "PILOTO_COMPARATIVO")
    if tipo != payload.get("tipo_piloto", "PILOTO_COMPARATIVO"):
        return None
    if tipo == "PILOTO_TECNICO":
        from .importador import exigir_local
        try:
            exigir_local()
        except ValueError:
            return None
    if (payload.get("clave_funcional") != clave_funcional(datos)
            or payload.get("request_id") != datos["request_id"]
            or any(payload.get(k) != datos[k] for k in CAMPOS_TAREA)):
        return None
    return evento


def autorizar_y_encolar(resultado, request_id=None):
    if resultado["estado"] != "PILOTO_APTO":
        raise ValueError("PILOTO_NO_APTO")
    if request_id is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", request_id):
        raise ValueError("REQUEST_ID_INVALIDO")
    datos = dict(resultado["datos_tarea"], request_id=_request_id(request_id))
    tipo = resultado.get("tipo_piloto", "PILOTO_COMPARATIVO")
    if tipo == "PILOTO_TECNICO":
        from .importador import exigir_local
        exigir_local()
        datos["tipo_piloto"] = tipo
    clave = clave_funcional(datos)
    # Reutiliza correlacion/autorizacion de la misma revision para verificar idempotencia.
    existente = AuditLog.objects.filter(solicitud_id=datos["solicitud_id"], event=AUTORIZACION,
                                        payload__clave_funcional=clave).order_by("id").first()
    if existente:
        datos = {k: existente.payload[k] for k in CAMPOS_TAREA}
        if tipo == "PILOTO_TECNICO":
            datos["tipo_piloto"] = tipo
        permiso = existente
    else:
        previo = AuditLog.objects.filter(solicitud_id=datos["solicitud_id"], event__in=TERMINALES,
                                         payload__clave_funcional=clave).order_by("id").first()
        if previo:
            return {"estado": "YA_EVALUADO_FUERA_DEL_PILOTO", "request_id": previo.payload["request_id"],
                    "clave_funcional": clave, "solicitud_id": datos["solicitud_id"]}
        permiso = AuditLog.objects.create(solicitud_id=datos["solicitud_id"], event=AUTORIZACION,
            request_id=datos["request_id"][:120], payload=dict(datos, clave_funcional=clave,
                autorizado_decision_crediticia=False, invariantes_antes=capturar_invariantes(datos["solicitud_id"])))
    datos["piloto_id"] = str(permiso.pk)
    estado = encolar_shadow(**datos)
    return {"estado": estado, "solicitud_id": datos["solicitud_id"], "request_id": datos["request_id"],
            "piloto_id": datos["piloto_id"], "clave_funcional": clave}


def evidencia_invariantes(permiso):
    antes = permiso.payload["invariantes_antes"]
    despues = capturar_invariantes(permiso.solicitud_id)
    return {"antes": antes, "despues": despues, "alteracion_productiva": antes != despues}


def reporte_piloto(solicitud_id, request_id):
    eventos = AuditLog.objects.filter(solicitud_id=solicitud_id, payload__request_id=request_id)
    permiso = eventos.filter(event=AUTORIZACION).order_by("id").first()
    if permiso is None:
        raise ValueError("PILOTO_NO_ENCONTRADO")
    terminal = eventos.filter(event__in=TERMINALES, payload__clave_funcional=permiso.payload["clave_funcional"]).order_by("-id").first()
    contenido = terminal.payload if terminal else {}
    actual = capturar_invariantes(solicitud_id)
    tecnico = permiso.payload.get("tipo_piloto") == "PILOTO_TECNICO"
    return {
        "tipo_piloto": "PILOTO_TECNICO" if tecnico else "PILOTO_COMPARATIVO",
        "decision_productiva_baseline": "NO_DISPONIBLE" if tecnico else permiso.payload["invariantes_antes"]["decision_productiva"],
        "comparacion_productiva": "NO_APLICA" if tecnico else "BEFORE_AFTER",
        "solicitud_id": str(solicitud_id), "request_id": request_id,
        "fechaEvaluacion": permiso.payload["fecha_evaluacion"],
        "version_politica": permiso.payload["version_politica"],
        "version_contrato": contenido.get("version_contrato_recibida"),
        "estado_operativo": contenido.get("estado_operativo", "PENDIENTE"),
        "estado_global": contenido.get("estado_global"), "motivos": contenido.get("motivos", []),
        "reglas": [{k: r.get(k) for k in ("codigo", "estado", "motivo")} for r in contenido.get("reglas", [])],
        "errores_tecnicos": contenido.get("errores_tecnicos", []),
        "categoria_error": contenido.get("categoria_error"),
        "evidencia": contenido.get("reporte_piloto"),
        "fechas_evidencia": contenido.get("fechas_evidencia"),
        "autorizado_decision_crediticia": False,
        "estado_productivo_actual": actual["estado_solicitud"],
        "decision_productiva_actual": actual["decision_productiva"],
        "invariantes": contenido.get("invariantes"),
        "alteracion_productiva": contenido.get("invariantes", {}).get("alteracion_productiva"),
        "cambios_desde_autorizacion_hasta_consulta": actual != permiso.payload["invariantes_antes"],
        "clave_funcional": permiso.payload["clave_funcional"],
    }
