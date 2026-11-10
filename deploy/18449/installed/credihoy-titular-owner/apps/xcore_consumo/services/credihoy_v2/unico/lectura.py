"""Consulta institucional read-only de resultados existentes, nunca replay/import."""

from datetime import timedelta

from django.conf import settings
from django.http import Http404
from django.utils import timezone
from rest_framework.response import Response

from apps.xcore_consumo.models import EvaluacionConsumo
from .canal import CanalBase
from .funcional import KEY, VERSION, leer_estado_funcional


def presentar(paquete):
    """Allowlist: no reenviar raw, snapshots, evidencias OTP ni contexto LINIX."""
    r = paquete["resultado"]
    keys = ("versionContrato", "versionFuncional", "solicitudId", "requestId", "fechaEvaluacion",
        "fuenteHechos", "informacionInsuficiente", "reglasPendientes", "decisionCrediticia",
        "conclusionFuncional", "autorizadoDecisionCrediticia", "radicacionLinix", "estadoTramite", "revisionHumanaHabilitada")
    result = {k: r[k] for k in keys}
    for nombre in ("capacidadMatematica", "cuotaParaCapacidad", "cuotaContractual", "gastosFamiliares"):
        campos = ("estado", "disponible", "valor") if nombre == "capacidadMatematica" else ("disponible", "valor")
        result[nombre] = {k: r[nombre][k] for k in campos}
    result["resultadoCapacidad"] = {k: r["resultadoCapacidad"][k] for k in ("estado", "motivos", "referenciaPolitica")}
    result["recogida"] = {k: r["recogida"][k] for k in ("estado", "motivos", "observacionAplicacion")}
    result["reglas"] = [{k: h[k] for k in ("codigo", "nombre", "resultado", "efecto", "motivos", "estadoEvaluacion", "datosFaltantes")}
        for h in r["reglas"]]
    result["secuencia"] = [{k: h[k] for k in ("paso", "estado")} for h in r["secuencia"]]
    return {**paquete, "resultado": result,
        "estadoObtencionDatos": [{k: h[k] for k in ("fuente", "estado")} for h in paquete["estadoObtencionDatos"]]}


def autorizadas(usuario):
    dias = getattr(settings, "CREDIHOY_RESULTADOS_RETENCION_DIAS", 0)
    if (getattr(settings, "CREDIHOY_RESULTADOS_LECTURA_ENABLED", False) is not True
            or not usuario.is_active or not usuario.has_perm("xcore_consumo.view_evaluacionconsumo")
            or type(dias) is not int or not 1 <= dias <= 3650):
        raise Http404
    return EvaluacionConsumo.objects.filter(solicitud__asesor=usuario,
        solicitud__producto="CREDIHOY UNICO (NUEVO)", created_at__gte=timezone.now() - timedelta(days=dias),
        input_snapshot__version=VERSION,
        resultados__credihoy_unico_funcional__fuenteHechos="EXPEDIENTE_OPERATIVO_SIN_DECISION",
        resultados__credihoy_unico_funcional__autorizadoDecisionCrediticia=False)


class ExpedientesLecturaView(CanalBase):
    def get(self, request):
        qs = autorizadas(request.user).order_by("-created_at", "pk")
        return Response({"versionContrato": "credihoy-expedientes-lectura-v1", "soloLectura": True,
            "expedientes": [{"solicitudId": str(e.solicitud_id), "fechaEvaluacion": e.created_at.isoformat()}
                for e in qs.only("solicitud_id", "created_at")[:100]]})


class ResultadoLecturaView(CanalBase):
    def get(self, request, solicitud_id):
        e = autorizadas(request.user).filter(solicitud_id=solicitud_id).select_related("solicitud").first()
        if e is None:
            raise Http404
        try:
            result = leer_estado_funcional(e.solicitud)
            r = result["resultado"]
            if (not isinstance(r, dict) or r.get("versionFuncional") != VERSION
                    or r.get("solicitudId") != str(solicitud_id) or r.get("decisionCrediticia") != "NO_AUTORIZADA"
                    or r.get("radicacionLinix") != "NO_EJECUTADA"):
                raise ValueError
            return Response(presentar(result))
        except (ValueError, KeyError, TypeError, AttributeError):
            return Response({"codigo": "RESULTADO_REQUIERE_REVISION"}, status=409)
        except Exception:
            return Response({"codigo": "RESULTADO_NO_RECUPERABLE"}, status=503)
