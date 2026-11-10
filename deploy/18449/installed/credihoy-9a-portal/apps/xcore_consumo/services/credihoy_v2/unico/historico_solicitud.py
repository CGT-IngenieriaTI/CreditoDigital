"""Solicitud/evaluacion local historica usando modelos existentes, sin radicacion."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from apps.documentos.models import AceptacionDocumento, DocumentoLegal
from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from .historico import HistoricoInvalido, evaluar_historico
from .laboratorio import PRODUCTO

VERSION = "CD5A-1"
DOCUMENTOS = (
    ("terminos", "TERMINOS", "Alcance del ensayo historico", "Ensayo local shadow. No constituye solicitud LINIX ni autorizacion crediticia."),
    ("datos", "DATOS", "Uso autorizado de evidencia historica", "Confirmo que el asociado y operador autorizaron reutilizar estos historicos exclusivamente en el ensayo local CD-5A."),
    ("consultas", "CENTRALES", "Sin nuevas consultas", "Confirmo la reproduccion de consultas almacenadas. No autorizo ni solicito nuevas consultas a centrales ni servicios financieros."),
)


class ConflictoHistorico(HistoricoInvalido):
    pass


class ResultadoHistoricoNoEncontrado(HistoricoInvalido):
    pass


def descriptor_historico():
    return {"modo": "HISTORICO_CONTROLADO", "contexto": {"documentos": [
        {"id": code, "titulo": titulo, "version": VERSION, "requerido": True, "contenido": texto, "url": None}
        for code, _, titulo, texto in DOCUMENTOS]},
        "condicionesProducto": {"estado": "INFORMADAS_POR_BACKEND", "plazoMinimoMeses": 1,
            "plazoMaximoMeses": 48, "montoMinimo": None, "montoMaximo": None, "periodicidades": ["MENSUAL"]},
        "consultaObligaciones": {"estado": "PENDIENTE_CONSULTA", "obligaciones": []}}


def validar_formulario(p, datos):
    if (type(p) is not dict or set(p) != {"modo", "producto", "solicitante", "finanzasDeclaradas", "solicitud", "consentimientos"}
            or p["modo"] != "HISTORICO_CONTROLADO" or p["producto"] != PRODUCTO):
        raise HistoricoInvalido("REQUEST_INVALIDO")
    persona, solicitud, finanzas = p["solicitante"], p["solicitud"], p["finanzasDeclaradas"]
    if (type(persona) is not dict or set(persona) != {"tipoIdentificacion", "identificacion", "nombres", "apellidos", "celular", "correo"}
            or persona["identificacion"] != datos.documento or persona["tipoIdentificacion"] != datos.tipo_documento):
        raise HistoricoInvalido("IDENTIDAD_NO_AUTORIZADA")
    if any(type(v) is not str or len(v) > 255 for v in persona.values()):
        raise HistoricoInvalido("DATOS_PERSONALES_INVALIDOS")
    apellidos_autorizados = {datos.apellido.strip().upper()}
    if datos.apellidos_completos:
        apellidos_autorizados.add(datos.apellidos_completos.strip().upper())
    if persona["apellidos"] and persona["apellidos"].strip().upper() not in apellidos_autorizados:
        raise HistoricoInvalido("IDENTIDAD_NO_AUTORIZADA")
    if (type(solicitud) is not dict or set(solicitud) != {"monto", "plazoMeses", "periodicidadPago", "recogerCredihoy", "obligacionRecogidaReferencia"}
            or type(solicitud["monto"]) is not str or not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,2})?", solicitud["monto"])
            or Decimal(solicitud["monto"]) <= 0 or type(solicitud["plazoMeses"]) is not int
            or not 1 <= solicitud["plazoMeses"] <= 999 or solicitud["periodicidadPago"] != "MENSUAL"
            or type(solicitud["recogerCredihoy"]) is not bool):
        raise HistoricoInvalido("PARAMETROS_SOLICITUD_INVALIDOS")
    ref = solicitud["obligacionRecogidaReferencia"]
    if (solicitud["recogerCredihoy"] and (type(ref) is not str or not ref.strip() or len(ref) > 64)
            or not solicitud["recogerCredihoy"] and ref is not None):
        raise HistoricoInvalido("SELECCION_RECOGIDA_INVALIDA")
    if type(finanzas) is not dict or set(finanzas) != {"ingresos", "otrosIngresos", "gastos"}:
        raise HistoricoInvalido("DECLARACION_INVALIDA")
    for v in finanzas.values():
        if type(v) is not str or (v != "" and not re.fullmatch(r"[0-9]{1,18}(?:\.[0-9]{1,2})?", v)):
            raise HistoricoInvalido("DECLARACION_INVALIDA")
    expected = {code for code, _, _, _ in DOCUMENTOS}
    consent = p["consentimientos"]
    if (type(consent) is not list or len(consent) != len(expected) or any(type(c) is not dict or
            set(c) != {"id", "version", "aceptado"} or c["id"] not in expected or c["version"] != VERSION
            or c["aceptado"] is not True for c in consent) or len({c["id"] for c in consent}) != len(expected)):
        raise HistoricoInvalido("CONSENTIMIENTO_REPRODUCCION_AUSENTE")
    return p


def _operacion(usuario, clave):
    try:
        if str(UUID(clave)) != clave:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise HistoricoInvalido("IDEMPOTENCIA_INVALIDA") from None
    return uuid5(NAMESPACE_URL, "cd5a:" + str(usuario.pk) + ":" + clave)


def _resultado_persistido(previa, request_id):
    ev = EvaluacionConsumo.objects.filter(solicitud=previa).first()
    if ev is None or not isinstance(ev.resultados, dict) or not ev.resultados.get("credihoy_unico_historico"):
        raise ConflictoHistorico("RESULTADO_NO_RECUPERABLE")
    resultado = deepcopy(ev.resultados["credihoy_unico_historico"])
    if (not isinstance(resultado, dict) or resultado.get("solicitudId") != str(previa.pk)
            or resultado.get("fuenteHechos") != "HISTORICO_CONTROLADO"
            or resultado.get("modo") != "HISTORICO_CONTROLADO"
            or resultado.get("versionContrato") != "credihoy-shadow-lab-v1"
            or resultado.get("decisionCrediticia") != "NO_AUTORIZADA"
            or resultado.get("autorizadoDecisionCrediticia") is not False
            or resultado.get("radicacionLinix") != "NO_EJECUTADA"
            or resultado.get("conclusionFuncional") != "NO_DETERMINADA"):
        raise ConflictoHistorico("RESULTADO_NO_RECUPERABLE")
    aceptadas = AceptacionDocumento.objects.filter(solicitud=previa, aceptado=True).select_related("documento")
    documentos = {a.documento.codigo: a.documento for a in aceptadas}
    if any("cd5a-" + code not in documentos or documentos["cd5a-" + code].version != VERSION
            or documentos["cd5a-" + code].descripcion != texto for code, _, _, texto in DOCUMENTOS):
        raise ConflictoHistorico("CONSENTIMIENTO_NO_RECUPERABLE")
    resultado["requestId"] = request_id
    return resultado


def recuperar_resultado(*, usuario, request_id, clave):
    """Solo lectura del mismo usuario/operacion: nunca recrear ni reevaluar."""
    datos = settings.CREDIHOY_HISTORICOS
    datos.verificar_integridad(settings.CREDIHOY_HISTORICO_PRE_PATH, settings.CREDIHOY_HISTORICO_HDC_PATH,
        getattr(settings, "CREDIHOY_HISTORICO_FECHA_PATH", None), getattr(settings, "CREDIHOY_HISTORICO_LINIX_PATH", None),
        getattr(settings, "CREDIHOY_HISTORICO_S14_PATH", None),
        getattr(settings, "CREDIHOY_HISTORICO_CONFIRMACION_SALARIAL_PATH", None))
    previa = Solicitud.objects.filter(pk=_operacion(usuario, clave), asesor=usuario).first()
    if previa is None:
        raise ResultadoHistoricoNoEncontrado("RESULTADO_HISTORICO_NO_ENCONTRADO")
    meta = previa.metadatos
    if (previa.producto != PRODUCTO or meta.get("modo") != "HISTORICO_CONTROLADO"
            or meta.get("huella_fuentes") != datos.huella
            or meta.get("fecha_evaluacion") != settings.CREDIHOY_HISTORICO_FECHA_EVALUACION.isoformat()):
        raise ConflictoHistorico("OPERACION_REQUIERE_CONCILIACION")
    return _resultado_persistido(previa, request_id)


def reproducir_solicitud(p, *, usuario, request_id, clave):
    datos = settings.CREDIHOY_HISTORICOS
    if datos.fecha_expedicion is None:
        raise HistoricoInvalido("FECHA_EXPEDICION_VERIFICADA_REQUERIDA")
    datos.verificar_integridad(settings.CREDIHOY_HISTORICO_PRE_PATH, settings.CREDIHOY_HISTORICO_HDC_PATH,
        getattr(settings, "CREDIHOY_HISTORICO_FECHA_PATH", None), getattr(settings, "CREDIHOY_HISTORICO_LINIX_PATH", None),
        getattr(settings, "CREDIHOY_HISTORICO_S14_PATH", None),
        getattr(settings, "CREDIHOY_HISTORICO_CONFIRMACION_SALARIAL_PATH", None))
    validar_formulario(p, datos)
    operation = _operacion(usuario, clave)
    huella = hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    fecha = settings.CREDIHOY_HISTORICO_FECHA_EVALUACION
    if not isinstance(fecha, datetime) or fecha.utcoffset() is None:
        raise HistoricoInvalido("FECHA_EVALUACION_INVALIDA")
    try:
        with transaction.atomic():
            previa = Solicitud.objects.filter(pk=operation).first()
            if previa is not None:
                meta = previa.metadatos
                if (previa.asesor_id != usuario.pk or meta.get("modo") != "HISTORICO_CONTROLADO"
                        or meta.get("huella_formulario") != huella or meta.get("huella_fuentes") != datos.huella
                        or meta.get("fecha_evaluacion") != fecha.isoformat()):
                    raise ConflictoHistorico("OPERACION_REQUIERE_CONCILIACION")
                return _resultado_persistido(previa, request_id), True
            persona, created = Solicitante.objects.get_or_create(tipo_identificacion=datos.tipo_documento,
                numero_identificacion=datos.documento, defaults={"primer_apellido": datos.apellido,
                    "fecha_expedicion": datos.fecha_expedicion, "celular": p["solicitante"]["celular"], "email": p["solicitante"]["correo"]})
            if not created and (persona.primer_apellido != datos.apellido or persona.fecha_expedicion != datos.fecha_expedicion):
                raise ConflictoHistorico("SOLICITANTE_EXISTENTE_INCONSISTENTE")
            solicitud = Solicitud.objects.create(pk=operation, solicitante=persona, asesor=usuario,
                producto=PRODUCTO, estado="INICIADA", paso_actual="CREDIHOY_SHADOW_EVALUADO",
                monto_solicitado=Decimal(p["solicitud"]["monto"]), plazo_solicitado=p["solicitud"]["plazoMeses"],
                metadatos={"modo": "HISTORICO_CONTROLADO", "version": VERSION, "huella_formulario": huella,
                    "huella_fuentes": datos.huella, "fecha_evaluacion": fecha.isoformat(),
                    "estado_evaluacion": "EVALUADO_SHADOW", "autorizado_decision_crediticia": False,
                    "radicacion_linix": "NO_EJECUTADA"})
            SolicitudConsumo.objects.create(solicitud=solicitud, estado="BORRADOR", documentos_autorizados=False,
                form_data={**p["solicitud"], "finanzasDeclaradas": p["finanzasDeclaradas"]},
                orchestration_data={"modo": "HISTORICO_CONTROLADO", "sin_consultas_externas": True})
            for code, tipo, titulo, texto in DOCUMENTOS:
                documento, _ = DocumentoLegal.objects.get_or_create(codigo="cd5a-" + code,
                    defaults={"tipo_documento": tipo, "titulo": titulo, "descripcion": texto, "version": VERSION})
                if documento.version != VERSION or documento.descripcion != texto:
                    raise ConflictoHistorico("DOCUMENTO_REPRODUCCION_INCONSISTENTE")
                AceptacionDocumento.objects.create(solicitud=solicitud, documento=documento,
                    aceptado=True, fecha_aceptacion=timezone.now())
            resultado = evaluar_historico(datos, p["solicitud"], request_id, fecha)
            resultado.update(solicitudId=str(solicitud.pk), estadoSolicitud="EVALUADO_SHADOW", modo="HISTORICO_CONTROLADO")
            EvaluacionConsumo.objects.create(solicitud=solicitud,
                input_snapshot={"modo": "HISTORICO_CONTROLADO", "form_data": p["solicitud"],
                    "finanzasDeclaradas": p["finanzasDeclaradas"], "naturaleza_declaracion": "NO_CERTIFICADA"},
                integraciones_snapshot={"modo": "HISTORICO_CONTROLADO", "huella_fuentes": datos.huella,
                    "fecha_pre_original": datos.fecha_pre_original, "fecha_hdc_original": datos.fecha_hdc_original},
                resultados={"credihoy_unico_historico": resultado}, decision_final="NO_AUTORIZADA")
            AuditLog.objects.create(solicitud=solicitud, event="CREDIHOY_HISTORICO_EVALUADO", request_id=request_id,
                actor="usuario:" + str(usuario.pk), payload={"modo": "HISTORICO_CONTROLADO",
                    "estado": "EVALUADO_SHADOW", "huella_fuentes": datos.huella,
                    "autorizado_decision_crediticia": False, "radicacion_linix": "NO_EJECUTADA"})
            return resultado, False
    except (IntegrityError, OperationalError):
        # Unique operation PK prevents duplicate requests; SQLite is not PostgreSQL certification.
        raise ConflictoHistorico("OPERACION_REQUIERE_CONCILIACION") from None
