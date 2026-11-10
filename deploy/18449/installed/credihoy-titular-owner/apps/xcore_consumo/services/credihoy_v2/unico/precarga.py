"""Precarga asistida del piloto. Sin identidad digital ni metricas financieras."""
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from apps.integraciones.risk_gateway.operaciones.validacion import huella
from apps.utils.models import AuditLog
from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text, OTPCryptoError
from ..cliente import ApiDataCrediHoyV2Client, ConfiguracionApiDataV2
from .consentimiento_real import habilitar

SESSION_KEY = "credihoy_precarga_asistida"
VERSION = "credihoy-precarga-piloto-v1"


class DatosPrecargaInvalidos(ValueError):
    def __init__(self, campo, valor):
        self.campo = campo
        self.tipo = type(valor).__name__
        super().__init__("DATOS_ASOCIADO_INCOMPATIBLES")


def _entero_exacto(value, campo):
    if type(value) is str:
        text = value.strip()
        if not re.fullmatch(r"[0-9]{1,32}(?:\.0{1,8})?", text):
            raise DatosPrecargaInvalidos(campo, value)
        value = Decimal(text)
    if type(value) is int:
        result = value
    elif (type(value) is Decimal and value.is_finite() and 0 <= value < 10 ** 32
            and value == value.to_integral_value()):
        result = int(value)
    else:
        raise DatosPrecargaInvalidos(campo, value)
    if not 0 <= result < 10 ** 32:
        raise DatosPrecargaInvalidos(campo, value)
    return result


def habilitar_precarga(operador):
    habilitar(operador)
    if getattr(settings, "CREDIHOY_PRECARGA_ASISTIDA_ENABLED", False) is not True:
        raise OperacionError("PRECARGA_NO_HABILITADA")


def _texto(raw, key):
    value = raw.get(key)
    if value is None or value == "":
        return None
    if key in {"ciudad", "estadoCivil", "generoSocio"} and type(value) in {int, Decimal}:
        value = str(_entero_exacto(value, key))
    if type(value) is not str or len(value) > 255:
        raise DatosPrecargaInvalidos(key, value)
    return value.strip() or None


def _proyectar(raw):
    keys = ("primerNombre", "segundoNombre", "primerApellido", "segundoApellido", "direccion", "barrio", "ciudad",
            "estadoCivil", "generoSocio")
    datos = {k: _texto(raw, k) for k in keys}
    phone = raw.get("movilTelefono")
    if type(phone) in {int, Decimal}:
        phone = str(_entero_exacto(phone, "movilTelefono"))
    if phone is not None and (type(phone) is not str or len(phone) > 32):
        raise DatosPrecargaInvalidos("movilTelefono", phone)
    datos["celular"] = phone or None
    birth = _texto(raw, "fechaNacimiento")
    try:
        datos["fechaNacimiento"] = datetime.strptime(birth, "%d/%m/%Y").date().isoformat() if birth else None
    except ValueError:
        raise DatosPrecargaInvalidos("fechaNacimiento", birth) from None
    if datos["fechaNacimiento"] and datos["fechaNacimiento"] > timezone.localdate().isoformat():
        raise DatosPrecargaInvalidos("fechaNacimiento", birth)
    count = raw.get("personasACargo")
    if type(count) is str and not count.strip():
        count = None
    if count is not None:
        count = _entero_exacto(count, "personasACargo")
    datos["personasACargo"] = count
    correo = _texto(raw, "email")
    if correo is not None:
        try:
            validate_email(correo)
        except ValidationError:
            raise DatosPrecargaInvalidos("email", correo) from None
    datos["correo"] = correo
    issued = _texto(raw, "fechaExpedicion")
    try:
        datos["fechaExpedicion"] = datetime.strptime(issued, "%d/%m/%Y").date().isoformat() if issued else None
    except ValueError:
        raise DatosPrecargaInvalidos("fechaExpedicion", issued) from None
    if datos["fechaExpedicion"] and datos["fechaExpedicion"] > timezone.localdate().isoformat():
        raise DatosPrecargaInvalidos("fechaExpedicion", issued)
    return datos


def precargar(request, payload):
    habilitar_precarga(request.user)
    if (type(payload) is not dict or set(payload) != {"numeroDocumento", "identidadPresencial"}
            or type(payload["numeroDocumento"]) is not str
            or not re.fullmatch(r"[0-9]{6,15}", payload["numeroDocumento"])):
        raise ValueError("REQUEST_INVALIDO")
    proof = payload["identidadPresencial"]
    if (type(proof) is not dict or set(proof) != {"verificada", "referencia"} or proof["verificada"] is not True):
        raise OperacionError("VERIFICACION_PRESENCIAL_REQUERIDA")
    referencia = str(UUID(proof["referencia"]))
    request_id = str(UUID(request.headers.get("X-Request-ID", "")))
    request.session.pop(SESSION_KEY, None)
    # Se registra la comprobacion ANTES de acceder a la informacion personal.
    identity = huella("precarga-identidad", payload["numeroDocumento"])
    audit = AuditLog.objects.create(event="credihoy_precarga_identidad_presencial", actor=f"user:{request.user.pk}",
        request_id=request_id, payload={"referencia": referencia, "identidadHuella": identity,
            "metodo": "PRESENCIAL_OPERADOR_PILOTO", "identidadDigitalCertificada": False})
    client = ApiDataCrediHoyV2Client(ConfiguracionApiDataV2.desde_settings())
    raw = client.consultar_socio(numero_documento=payload["numeroDocumento"], request_id=request_id)
    datos = _proyectar(raw)
    now = timezone.now()
    result = {"versionContrato": VERSION, "referenciaPrecarga": request_id, "fechaConsulta": now.isoformat(),
        "numeroDocumento": payload["numeroDocumento"], "identificacionFuente": "REQUEST",
        "fuente": "API_DATA.consultarSocio", "estado": "DATOS_PRECARGADOS", "datos": datos,
        "verificacionIdentidad": {"metodo": "PRESENCIAL_OPERADOR_PILOTO", "identidadDigitalCertificada": False,
            "referencia": referencia}, "camposFaltantes": [k for k, v in datos.items() if v is None],
        "autorizadoDecisionCrediticia": False, "radicacionLinix": "NO_EJECUTADA"}
    request.session[SESSION_KEY] = encrypt_text(json.dumps({"operador": request.user.pk, "identidad": identity,
        "auditId": audit.pk, "resultado": result}))
    request.session.set_expiry(600)
    return result


def recuperar(request):
    habilitar_precarga(request.user)
    try:
        value = json.loads(decrypt_text(request.session[SESSION_KEY]))
        consulta = datetime.fromisoformat(value["resultado"]["fechaConsulta"])
        if (value["operador"] != request.user.pk or consulta.utcoffset() is None
                or not timezone.now() - timedelta(minutes=10) <= consulta <= timezone.now()):
            raise ValueError
        return value
    except (KeyError, ValueError, TypeError, OTPCryptoError):
        request.session.pop(SESSION_KEY, None)
        raise OperacionError("PRECARGA_EXPIRADA_O_NO_DISPONIBLE") from None


def validar_para_creacion(request, titular, referencia, confirmados):
    value = recuperar(request)
    p, datos = value["resultado"], value["resultado"]["datos"]
    if (confirmados is not True or referencia != p["referenciaPrecarga"]
            or titular.get("tipo_identificacion") != "CC"
            or value["identidad"] != huella("precarga-identidad", titular.get("numero_identificacion"))
            or (datos["primerApellido"] is not None and titular.get("primer_apellido") != datos["primerApellido"])):
        raise OperacionError("PRECARGA_NO_CORRESPONDE_AL_TITULAR")
    return {"fuente": p["fuente"], "fechaConsulta": p["fechaConsulta"], "referencia": referencia,
        "auditIdentidadId": value["auditId"], "metodoIdentidad": "PRESENCIAL_OPERADOR_PILOTO",
        "identidadDigitalCertificada": False, "datosConfirmados": True,
        "snapshotCifrado": request.session[SESSION_KEY],
        "apellidoDeclarado": datos["primerApellido"] is None,
        "contactoModificado": titular.get("celular") != datos["celular"]}


def recuperar_nacimiento_evidenciado(solicitud):
    """Recupera el dato cifrado del expediente; admite sesiones legacy.

    La caducidad de acceso no convierte el dato en una nueva consulta ni
    certifica identidad digital. Se exige la traza asociada a esta solicitud.
    """
    from django.contrib.sessions.models import Session
    from datetime import date
    from ..base import Hecho, EstadoCertificacion as EC
    proof = solicitud.metadatos.get("precargaLinix", {})
    ausente = Hecho(fuente="API_DATA.consultarSocio",
        ruta_origen="informacionSocio.fechaNacimiento",
        observacion="NACIMIENTO_PRECARGA_NO_RECUPERABLE_CON_TRAZABILIDAD")
    if (proof.get("fuente") != "API_DATA.consultarSocio"
            or proof.get("metodoIdentidad") != "PRESENCIAL_OPERADOR_PILOTO"
            or not AuditLog.objects.filter(pk=proof.get("auditIdentidadId"), solicitud=solicitud,
                actor=f"user:{solicitud.asesor_id}", event="credihoy_precarga_identidad_presencial").exists()):
        return ausente
    matches = []
    snapshot = proof.get("snapshotCifrado")
    if snapshot is not None:
        tokens = (snapshot,)
    else:
        tokens = (session.get_decoded().get(SESSION_KEY) for session in Session.objects.all().iterator())
    for token in tokens:
        if not token:
            continue
        try:
            value = json.loads(decrypt_text(token))
            result = value["resultado"]
            if (value["operador"] != solicitud.asesor_id
                    or value["auditId"] != proof["auditIdentidadId"]
                    or value["identidad"] != huella("precarga-identidad", solicitud.solicitante.numero_identificacion)
                    or result["numeroDocumento"] != solicitud.solicitante.numero_identificacion
                    or result["referenciaPrecarga"] != proof["referencia"]
                    or result["fechaConsulta"] != proof["fechaConsulta"]
                    or result["fuente"] != proof["fuente"]
                    or result["verificacionIdentidad"]["metodo"] != proof["metodoIdentidad"]):
                continue
            text = result["datos"].get("fechaNacimiento")
            if type(text) is not str or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
                continue
            birth = date.fromisoformat(text)
            queried = datetime.fromisoformat(result["fechaConsulta"])
            if queried.utcoffset() is None or queried > timezone.now() or birth > queried.date():
                continue
            matches.append((birth, queried))
        except (KeyError, ValueError, TypeError, OTPCryptoError):
            continue
    if not matches or len(set(matches)) != 1:
        return ausente
    birth, queried = matches[0]
    return Hecho(valor=birth, disponible=True, fuente="API_DATA.consultarSocio",
        referencia_evidencia=proof["referencia"], ruta_origen="informacionSocio.fechaNacimiento",
        fecha_consulta=queried, estado_certificacion=EC.CERTIFICADO,
        observacion="CAMPO_FECHA_NACIMIENTO_Y_TRAZA_API_VERIFICADOS; NO_CERTIFICA_IDENTIDAD_DIGITAL")
