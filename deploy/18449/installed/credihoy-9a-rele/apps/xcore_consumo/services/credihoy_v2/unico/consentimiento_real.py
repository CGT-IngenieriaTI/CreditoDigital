"""Firma especifica del piloto con ConsentimientoConsumo y OTP existentes."""

import hashlib
import hmac
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.solicitudes.models import Solicitud, EstadoSolicitud
from apps.usuarios.models import Solicitante, TipoIdentificacion
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import ConsentimientoConsumo, EstadoOtp, SolicitudConsumo
from apps.xcore_consumo.services.consentimiento_integridad import build_consent_hash
from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text
from apps.integraciones.risk_gateway.operaciones.validacion import huella
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from .consulta_controlada import PRODUCTO, VERSION_CONSENTIMIENTO, texto_consentimiento_controlado

WORKFLOW = "CREDIHOY_REAL_OTP_1"


def habilitar(operador):
    if (getattr(settings, "CREDIHOY_CONSENTIMIENTO_REAL_ENABLED", False) is not True
            or not operador.is_authenticated or not operador.is_active or not operador.is_staff):
        raise OperacionError("CONSENTIMIENTO_REAL_NO_HABILITADO")
    permitido = getattr(settings, "CREDIHOY_FIRMA_OPERADOR_USERNAME", None)
    if permitido is not None and operador.username != permitido:
        raise OperacionError("OPERADOR_FIRMA_NO_AUTORIZADO")


def obtener(solicitud_id, operador, *, lock=False):
    habilitar(operador)
    limitado = getattr(settings, "CREDIHOY_FIRMA_SOLICITUD_ID", None)
    if limitado is not None and str(solicitud_id) != limitado:
        raise OperacionError("SOLICITUD_FIRMA_NO_AUTORIZADA")
    qs = Solicitud.objects.select_related("solicitante")
    if lock:
        qs = qs.select_for_update()
    s = qs.get(pk=solicitud_id, asesor=operador)
    if s.producto != PRODUCTO or s.metadatos.get("modo") != "CONSULTA_REAL_CONTROLADA":
        raise OperacionError("SOLICITUD_REAL_CONTROLADA_REQUERIDA")
    return s


def crear_desde_expediente(origen_id, clave, operador):
    habilitar(operador)
    key = UUID(str(clave))
    with transaction.atomic():
        origen = Solicitud.objects.select_for_update().get(pk=origen_id, asesor=operador)
        if origen.metadatos.get("modo") != "HISTORICO_CONTROLADO":
            raise OperacionError("EXPEDIENTE_HISTORICO_REQUERIDO")
        existente = Solicitud.objects.filter(pk=key).first()
        if existente:
            if (existente.asesor_id != operador.pk or existente.solicitante_id != origen.solicitante_id
                    or existente.metadatos.get("origenConsentimiento") != str(origen.pk)):
                raise OperacionError("CLAVE_PREPARACION_INCOMPATIBLE")
            return obtener(key, operador)
        s = Solicitud.objects.create(id=key, asesor=operador, solicitante=origen.solicitante,
            producto=PRODUCTO, monto_solicitado=origen.monto_solicitado,
            plazo_solicitado=origen.plazo_solicitado,
            metadatos={"modo": "CONSULTA_REAL_CONTROLADA", "origenConsentimiento": str(origen.pk)})
        SolicitudConsumo.objects.create(solicitud=s, estado="CONSENTIMIENTO_PENDIENTE")
        AuditLog.objects.create(solicitud=s, event="credihoy_consentimiento_expediente",
            actor=f"user:{operador.pk}", payload={"workflow": WORKFLOW})
        return s


def crear_para_titular(payload, operador, *, precarga=None):
    """Captura independiente, sin clonar historicos ni otorgar consentimiento."""
    habilitar(operador)
    if getattr(settings, "CREDIHOY_NUEVO_TITULAR_ENABLED", False) is not True:
        raise OperacionError("CAPTURA_TITULAR_NO_HABILITADA")
    from rest_framework import serializers
    from apps.utils.validators import validate_document_issue_date
    from apps.utils.titular import _validate_primer_apellido, _normalize_colombia_mobile
    from django.contrib.auth import get_user_model

    class TitularSerializer(serializers.Serializer):
        clave = serializers.UUIDField()
        tipo_identificacion = serializers.ChoiceField(choices=TipoIdentificacion.choices)
        numero_identificacion = serializers.RegexField(r"^\d{6,15}$")
        primer_apellido = serializers.CharField(max_length=80, validators=[_validate_primer_apellido])
        fecha_expedicion = serializers.DateField(validators=[validate_document_issue_date])
        celular = serializers.CharField(max_length=20)
        email = serializers.EmailField()
        monto = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
        plazo = serializers.IntegerField(min_value=1, max_value=48)

    data = TitularSerializer(data=payload)
    data.is_valid(raise_exception=True)
    values = data.validated_data
    key = values.pop("clave")
    limitado = getattr(settings, "CREDIHOY_FIRMA_SOLICITUD_ID", None)
    if limitado is not None and str(key) != limitado:
        raise OperacionError("SOLICITUD_FIRMA_NO_AUTORIZADA")
    values["primer_apellido"] = _validate_primer_apellido(values["primer_apellido"])
    values["celular"] = _normalize_colombia_mobile(values["celular"])
    fingerprint = huella("captura-titular", {k: str(v) for k, v in values.items()})
    if precarga is not None:
        fingerprint = huella("captura-titular-precarga", {"captura": fingerprint, "precarga": precarga})
    monto, plazo = values.pop("monto"), values.pop("plazo")
    with transaction.atomic():
        # Serializa capturas del mismo operador; nunca sustituye la clave del POST.
        get_user_model().objects.select_for_update().get(pk=operador.pk)
        existente = Solicitud.objects.select_for_update().filter(pk=key).first()
        if existente:
            if (existente.asesor_id != operador.pk or existente.producto != PRODUCTO
                    or existente.metadatos.get("modo") != "CONSULTA_REAL_CONTROLADA"
                    or not hmac.compare_digest(existente.metadatos.get("capturaHuella", ""), fingerprint)):
                raise OperacionError("CLAVE_PREPARACION_INCOMPATIBLE")
            return existente
        applicant, _ = Solicitante.objects.get_or_create(
            tipo_identificacion=values["tipo_identificacion"], numero_identificacion=values["numero_identificacion"],
            defaults={k: v for k, v in values.items() if k not in {"tipo_identificacion", "numero_identificacion"}})
        applicant = Solicitante.objects.select_for_update().get(pk=applicant.pk)
        if any(getattr(applicant, k) != v for k, v in values.items()):
            raise OperacionError("TITULAR_EXISTENTE_NO_SOBRESCRIBIR")
        if Solicitud.objects.filter(solicitante=applicant, producto=PRODUCTO,
                metadatos__modo="CONSULTA_REAL_CONTROLADA").exists():
            raise OperacionError("EXPEDIENTE_CONTROLADO_EXISTENTE_RECUPERAR")
        s = Solicitud.objects.create(id=key, asesor=operador, solicitante=applicant,
            producto=PRODUCTO, monto_solicitado=monto, plazo_solicitado=plazo,
            estado=EstadoSolicitud.CONSENTIMIENTO_PENDIENTE, paso_actual="consentimiento",
            metadatos={"modo": "CONSULTA_REAL_CONTROLADA", "origen": "CAPTURA_TITULAR",
                "capturaHuella": fingerprint, "proveedores": ["HDC"], "productoLinix": "390",
                "modalidadLinix": "111", "destinacionLinix": "CFA",
                **({"precargaLinix": precarga} if precarga is not None else {})})
        SolicitudConsumo.objects.create(solicitud=s, estado="CONSENTIMIENTO_PENDIENTE")
        AuditLog.objects.create(solicitud=s, event="credihoy_titular_capturado",
            actor=f"user:{operador.pk}", payload={"workflow": WORKFLOW, "proveedores": ["HDC"], "firmado": False})
        if precarga is not None:
            AuditLog.objects.filter(pk=precarga["auditIdentidadId"], solicitud__isnull=True,
                actor=f"user:{operador.pk}").update(solicitud=s)
        return s


def _identidad(s):
    a = s.solicitante
    return huella("consentimiento-real-identidad", {"id": str(a.pk),
        "tipo": a.tipo_identificacion, "numero": a.numero_identificacion})


def validar_documento(s, c):
    texto = texto_consentimiento_controlado()
    if (c.version != VERSION_CONSENTIMIENTO or c.text_snapshot != texto
            or c.evidencia.get("workflow") != WORKFLOW
            or not hmac.compare_digest(c.evidencia.get("identidad", ""), _identidad(s))
            or c.evidencia.get("documentoSha256") != hashlib.sha256(texto.encode()).hexdigest()):
        raise OperacionError("CONSENTIMIENTO_ESPECIFICO_REAL_NO_VALIDO")


def aceptar(solicitud_id, operador, *, aceptado, contacto_aceptado, canal, destino):
    if getattr(settings, "CREDIHOY_PILOTO_ASISTIDO_ENABLED", False) is True and canal != "SMS":
        raise OperacionError("OTP_CANAL_PILOTO_NO_HABILITADO")
    if aceptado is not True or contacto_aceptado is not True or canal not in {"SMS", "EMAIL"}:
        raise OperacionError("ACEPTACION_EXPRESA_REQUERIDA")
    # Validacion del contacto antes de cifrar; nunca modifica al Solicitante historico.
    if canal == "SMS":
        from apps.xcore_consumo.services.otp import normalize_sms_destination
        destino = normalize_sms_destination(destino)
    else:
        from django.core.validators import validate_email
        validate_email(destino)
    with transaction.atomic():
        s = obtener(solicitud_id, operador, lock=True)
        c = ConsentimientoConsumo.objects.filter(solicitud=s).first()
        if c:
            validar_documento(s, c)
            if c.canal != canal or decrypt_text(c.evidencia["contactoCifrado"]) != destino:
                raise OperacionError("ACEPTACION_EXISTENTE_NO_SOBRESCRIBIR")
            return c
        texto = texto_consentimiento_controlado()
        c = ConsentimientoConsumo.objects.create(solicitud=s, version=VERSION_CONSENTIMIENTO,
            aceptado=True, firmado=False, canal=canal, fecha_aceptacion=timezone.now(),
            text_snapshot=texto, text_hash=build_consent_hash(VERSION_CONSENTIMIENTO, texto),
            tipo_firma="OTP_PENDIENTE", evidencia={"workflow": WORKFLOW, "estado": "OTP_PENDIENTE",
                "identidad": _identidad(s), "documentoSha256": hashlib.sha256(texto.encode()).hexdigest(),
                "tratamientoContactoAceptado": True, "contactoCifrado": encrypt_text(destino)})
        AuditLog.objects.create(solicitud=s, event="credihoy_consentimiento_aceptacion",
            actor=f"user:{operador.pk}", payload={"workflow": WORKFLOW, "version": c.version, "firmado": False})
        return c


def destino_otp(s, canal):
    c = s.consentimiento_consumo
    validar_documento(s, c)
    if not c.aceptado or c.canal != canal or not c.evidencia.get("tratamientoContactoAceptado"):
        raise OperacionError("ACEPTACION_CONTACTO_REQUERIDA")
    return decrypt_text(c.evidencia["contactoCifrado"])


def es_controlada(s):
    return s.producto == PRODUCTO and s.metadatos.get("modo") == "CONSULTA_REAL_CONTROLADA"


def accion_otp(solicitud_id, operador, *, accion, request, canal=None, codigo=None):
    from apps.xcore_consumo.services.otp import send_otp, verify_otp, get_otp_mode
    s = obtener(solicitud_id, operador)
    c = s.consentimiento_consumo
    validar_documento(s, c)
    if get_otp_mode() != "real":
        raise OperacionError("OTP_REAL_REQUERIDA")
    otp = s.active_otp_challenge
    verificada_vigente = (otp is not None and otp.expira_at and otp.expira_at > timezone.now())
    if c.firmado or (c.evidencia.get("estado") == "OTP_VERIFICADO" and verificada_vigente):
        return estado(solicitud_id, operador)
    if accion == "enviar" and canal != c.canal:
        raise OperacionError("CANAL_ACEPTADO_REQUERIDO")
    if accion == "enviar" and c.evidencia.get("estado") == "OTP_VERIFICADO":
        c.evidencia = {**c.evidencia, "estado": "OTP_PENDIENTE"}
        c.save(update_fields=["evidencia", "updated_at"])
    # Claim durable: un crash conserva el bloqueo; no reenvio automatico.
    claim = "OTP_ENVIANDO" if accion == "enviar" else "OTP_VERIFICANDO"
    won = ConsentimientoConsumo.objects.filter(pk=c.pk, tipo_firma="OTP_PENDIENTE").update(tipo_firma=claim)
    if not won:
        raise OperacionError("OTP_OPERACION_EN_CURSO_REQUIERE_RECUPERACION")
    error = None
    try:
        if accion == "enviar":
            send_otp(s, channel=canal, actor=f"user:{operador.pk}", request=request)
        else:
            verify_otp(s, code=codigo, actor=f"user:{operador.pk}", request=request)
    except Exception:
        error = "OTP_NO_ENVIADA" if accion == "enviar" else "OTP_NO_VALIDADA"
    finally:
        ConsentimientoConsumo.objects.filter(pk=c.pk, tipo_firma=claim).update(tipo_firma="OTP_PENDIENTE")
    if error:
        raise OperacionError(error)
    return estado(solicitud_id, operador)


def firmar(solicitud_id, operador):
    with transaction.atomic():
        s = obtener(solicitud_id, operador, lock=True)
        c = ConsentimientoConsumo.objects.select_for_update().get(solicitud=s)
        validar_documento(s, c)
        if c.firmado:
            return c
        otp = s.active_otp_challenge
        if (otp is None or otp.estado != EstadoOtp.VALIDADA or otp.solicitud_id != s.pk
                or otp.canal != c.canal
                or otp.provider not in {"twilio_verify", "internal_email"}
                or not otp.verificado_at or otp.verificado_at < c.fecha_aceptacion
                or not otp.expira_at or otp.expira_at <= timezone.now()
                or c.evidencia.get("estado") != "OTP_VERIFICADO"
                or c.evidencia.get("otpId") != str(otp.pk)):
            raise OperacionError("OTP_REAL_VERIFICADA_REQUERIDA")
        c.otp = otp
        c.text_hash = build_consent_hash(c.version, c.text_snapshot, otp.transaction_uuid, otp.canal, otp.verificado_at)
        c.firmado = True
        c.tipo_firma = "ACEPTACION_OTP"
        c.evidencia = {**c.evidencia, "estado": "CONSENTIMIENTO_FIRMADO",
            "fechaFirma": timezone.now().isoformat(), "transactionUuid": otp.transaction_uuid,
            "fechaAceptacionPreliminar": c.fecha_aceptacion.isoformat(),
            "proveedores": ["PRESELECTA", "HDC"], "maximoConsultasPorProveedor": 1}
        c.fecha_aceptacion = timezone.now()
        c.save()
        s.estado = "CONSENTIMIENTO_FIRMADO"
        s.paso_actual = "consentimiento"
        s.save(update_fields=["estado", "paso_actual", "updated_at"])
        SolicitudConsumo.objects.filter(solicitud=s).update(estado="CONSENTIMIENTO_FIRMADO")
        AuditLog.objects.create(solicitud=s, event="credihoy_consentimiento_firmado",
            actor=f"user:{operador.pk}", payload={"workflow": WORKFLOW, "version": c.version, "otpId": str(otp.pk)})
        return c


def estado(solicitud_id, operador):
    from apps.xcore_consumo.services.otp import mask_destination
    from .consulta_controlada import PROVEEDORES_CONSUMO, PRODUCTO_LINIX
    s = obtener(solicitud_id, operador)
    c = ConsentimientoConsumo.objects.filter(solicitud=s).first()
    if c:
        validar_documento(s, c)
    otp = s.active_otp_challenge
    now = timezone.now()
    return {"solicitudId": str(s.pk), "version": VERSION_CONSENTIMIENTO,
        "documento": texto_consentimiento_controlado(),
        "estado": c.evidencia["estado"] if c else "PENDIENTE_ACEPTACION",
        "firmado": bool(c and c.firmado), "fechaFirma": c.evidencia.get("fechaFirma") if c else None,
        "canal": c.canal if c else None, "operacionEnCurso": bool(c and c.tipo_firma in {"OTP_ENVIANDO", "OTP_VERIFICANDO"}),
        "destinoEnmascarado": mask_destination(c.canal, decrypt_text(c.evidencia["contactoCifrado"])) if c else None,
        "otp": None if otp is None else {"estado": otp.estado, "expiraAt": otp.expira_at,
            "intentos": otp.intentos, "maxIntentos": otp.max_intentos,
            "expirada": bool(otp.expira_at and otp.expira_at <= now),
            "reenvioEnSegundos": max(0, int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 30)
                - (now - otp.enviado_at).total_seconds()) + 1) if otp.enviado_at else 0},
        "alcanceConsumo": {"proveedores": list(PROVEEDORES_CONSUMO), "productoLinix": PRODUCTO_LINIX,
            "endpointHdc": "/api/internal/v1/hdc/consultar/", "preselectaPermitida": False},
        "consumoAutorizado": False, "autorizacionEconomica": "PENDIENTE_VALIDACION",
        "decisionCrediticia": "NO_AUTORIZADA", "radicacionLinix": "NO_EJECUTADA"}
