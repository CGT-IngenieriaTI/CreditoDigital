import json
import re
import unicodedata

from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.core.exceptions import ObjectDoesNotExist

from apps.xcore_consumo.models import EstadoOtp
from apps.xcore_consumo.services.flow import assert_consent_granted
from ..contratos import HDCRequest, PreselectaRequest
from .errores import ConsentimientoInvalido, IdentidadInconsistente, OperacionError


# PRESELECTA REST Tabla 2 pp.19-20 / HDC WSS 1.9 Tabla 1 p.18,
# contrastadas con los catalogos actuales del proveedor. No usar CE=2 del shadow legacy.
TIPOS_DOCUMENTO = {"CC": "1", "CE": "4", "PAS": "5", "TI": "7"}


def huella(ambito, valor):
    canonical = json.dumps(valor, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return salted_hmac("cd2b:" + ambito, canonical, algorithm="sha256").hexdigest()


def _apellido(valor):
    return unicodedata.normalize("NFC", valor).strip().casefold()


def validar(solicitud, request):
    if type(request) not in (PreselectaRequest, HDCRequest):
        raise OperacionError("DTO_GATEWAY_INVALIDO")
    if solicitud.producto == "CREDIHOY UNICO (NUEVO)" and type(request) is not HDCRequest:
        raise OperacionError("PRESELECTA_NO_APLICA_CREDIHOY_UNICO")
    # Revalidacion del contrato antes de persistir o consumir.
    type(request).desde_payload(request.a_dict())
    try:
        consent = solicitud.consentimiento_consumo
    except (ValueError,ObjectDoesNotExist):
        raise ConsentimientoInvalido() from None
    personal=consent.evidencia.get('workflow')=='COLO023_AUTOGESTION_V1'
    if personal:
        from apps.xcore_consumo.services.credihoy_v2.unico.centrales_personal import verificar_autorizacion_centrales_personal
        verified=verificar_autorizacion_centrales_personal(solicitud,consent)
    else:
        try:assert_consent_granted(solicitud)
        except ValueError:raise ConsentimientoInvalido() from None
    if consent.evidencia.get("workflow") in {"CREDIHOY_REAL_OTP_1","COLO023_AUTOGESTION_V1"}:
        from apps.xcore_consumo.services.credihoy_v2.unico.consulta_controlada import validar_politica_del_consumo
        validar_politica_del_consumo(solicitud)
        aprobacion = solicitud.metadatos.get("autorizacionEconomicaCredihoy")
        if (solicitud.metadatos.get("consumoRealAutorizado") is not True
                or type(aprobacion) is not dict or not aprobacion.get("referenciaCostos")
                or not aprobacion.get("responsableCondiciones") or not aprobacion.get("referenciaAutorizacionConsumo")
                or aprobacion.get("maximoConsultasPorProveedor") != 1):
            raise OperacionError("AUTORIZACION_INSTITUCIONAL_CONSUMO_PENDIENTE")
        if solicitud.producto == "CREDIHOY UNICO (NUEVO)" and aprobacion.get("proveedores") != ["HDC"]:
            raise OperacionError("AUTORIZACION_HDC_EXCLUSIVA_REQUERIDA")
        if aprobacion.get("vigenteHasta"):
            from datetime import datetime
            try:
                vigencia = datetime.fromisoformat(aprobacion["vigenteHasta"])
                if vigencia.utcoffset() is None or vigencia <= timezone.now():
                    raise ValueError
            except (TypeError, ValueError):
                raise OperacionError("AUTORIZACION_INSTITUCIONAL_CONSUMO_PENDIENTE") from None
    otp = consent.otp
    if not personal and (not consent.aceptado or not consent.firmado or not consent.version or not consent.text_snapshot.strip()
            or not re.fullmatch(r"[0-9a-fA-F]{64}", consent.text_hash)
            or consent.fecha_aceptacion > timezone.now() or otp is None
            or otp.solicitud_id != solicitud.pk or otp.estado != EstadoOtp.VALIDADA
            or otp.verificado_at is None or not otp.transaction_uuid
            or otp.verificado_at > consent.fecha_aceptacion):
        raise ConsentimientoInvalido()
    person = solicitud.solicitante
    expected_type = TIPOS_DOCUMENTO.get(person.tipo_identificacion)
    if expected_type is None:
        raise IdentidadInconsistente()
    data = request.a_dict()
    if type(request) is PreselectaRequest:
        identity = data["identidad"]
        document_type = identity["tipo_documento"]
        number, surname = identity["numero_documento"], identity["primer_apellido"]
        accepted_types = {expected_type, person.tipo_identificacion}
    else:
        if data.get("persona", "natural") != "natural":
            raise IdentidadInconsistente()
        document_type = data["person_id_type"]
        number, surname = data["person_id_number"], data.get("person_last_name", "")
        accepted_types = {expected_type}
    if (document_type not in accepted_types or number != person.numero_identificacion
            or not surname.strip() or _apellido(surname) != _apellido(person.primer_apellido)):
        raise IdentidadInconsistente()
    if personal:
        # Validate persisted personal provenance before reserving a paid report.
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import _formulario_persistido
        from apps.xcore_consumo.services.credihoy_v2.unico.autoservicio import validar_vinculo_consumo,validar_procedencia_formulario
        try:
            validar_vinculo_consumo(solicitud)
            form=_formulario_persistido(solicitud,solicitud.consumo_detail.form_data)
            source=validar_procedencia_formulario(solicitud,solicitud.consumo_detail.form_data)
            if (source['periodicidadPago']!=form['periodicidadPago']
                    or type(source['recogerCredihoy']) is not bool
                    or source['recogerCredihoy']!=form['recogerCredihoy']):raise ValueError
            if form['recogerCredihoy']:
                raise ValueError('REFERENCIA_RECOGIDA_INSTITUCIONAL_PENDIENTE')
        except (KeyError,TypeError,ValueError,ObjectDoesNotExist):
            raise OperacionError('FORMULARIO_EVALUACION_INCOMPLETO_ANTES_HDC') from None
    identity_hash = huella("identidad", {"solicitante": person.pk, "tipo": person.tipo_identificacion,
                                       "numero": number, "apellido": _apellido(surname)})
    proof=({'metodoAutorizacion':verified.metodo,'autorizacionReferencia':verified.referencia_verificacion,
        'textoSha256':verified.texto_sha256,'pdfSha256':verified.pdf_sha256,'verificacion':verified.confirmado_en.isoformat()}
        if personal else {'otp_id':otp.pk,'transaction_uuid':otp.transaction_uuid,'verificacion':otp.verificado_at.isoformat()})
    consent_hash = huella("consentimiento", {"id": consent.pk, "version": consent.version,
        "text_hash": consent.text_hash, "aceptacion": consent.fecha_aceptacion.isoformat(), **proof})
    return consent, identity_hash, consent_hash, huella("request", data)
