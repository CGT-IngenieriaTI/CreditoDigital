"""Preparacion local sin transporte; envio exige otra autorizacion especifica."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import hmac
import json

from django.conf import settings
from django.db import transaction

from apps.integraciones.risk_gateway.configuracion import GatewayConfig
from apps.integraciones.risk_gateway.contratos import HDCRequest
from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
from apps.integraciones.risk_gateway.operaciones.models import EstadoOperacion, OperacionGateway
from apps.integraciones.risk_gateway.operaciones.servicio import OrquestadorGateway, _enabled
from apps.integraciones.risk_gateway.operaciones.validacion import validar
from apps.solicitudes.models import Solicitud
from apps.xcore_consumo.models import ConsentimientoConsumo, EstadoOtp
from apps.xcore_consumo.services.consentimiento_integridad import build_consent_hash

PRODUCTO = "CREDIHOY UNICO (NUEVO)"
VERSION_CONSENTIMIENTO = "CREDIHOY-REAL-1"
PROVEEDORES_CONSUMO = ("HDC",)
PRODUCTO_LINIX = {"producto": "390", "modalidad": "111", "destinacion": "CFA"}


@dataclass(frozen=True)
class PoliticaConsumoInstitucional:
    """Commercial coverage is input evidence, never inferred from API access."""
    datos: dict = field(repr=False)

    @classmethod
    def desde_payload(cls, value, *, ahora=None):
        from django.utils import timezone
        import re
        expected={'version','id','revision','habilitada','producto','proveedor',
            'contratoReferencia','evidenciaComercialSha256','responsable','referenciaAprobacion',
            'esquema','costoUnitarioCOP','limitePorExpediente','cupoTotal','cupoDiario',
            'desde','hasta','revocada','referenciaConcurrencia'}
        if type(value) is not dict or set(value)!=expected:
            raise OperacionError('POLITICA_CONSUMO_CONTRATO_INVALIDO')
        if (value['version']!=1 or type(value['version']) is not int
                or type(value['revision']) is not int or value['revision']<1
                or value['habilitada'] is not True or value['revocada'] is not False
                or value['producto']!=PRODUCTO or value['proveedor']!='HDC'
                or type(value['limitePorExpediente']) is not int or value['limitePorExpediente']!=1
                or any(type(value[k]) is not int or not 1<=value[k]<=100000 for k in ('cupoTotal','cupoDiario'))
                or value['cupoDiario']>value['cupoTotal']):
            raise OperacionError('POLITICA_CONSUMO_NO_HABILITADA')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',str(value['id'])):
            raise OperacionError('POLITICA_CONSUMO_IDENTIFICADOR_INVALIDO')
        if (any(type(value[k]) is not str or not value[k].strip() or len(value[k])>128
                for k in ('contratoReferencia','responsable','referenciaAprobacion','referenciaConcurrencia'))
                or not re.fullmatch('[a-f0-9]{64}',str(value['evidenciaComercialSha256']))):
            raise OperacionError('COBERTURA_CONTRACTUAL_NO_ACREDITADA')
        try:
            if value['costoUnitarioCOP'] is not None and type(value['costoUnitarioCOP']) is not str:
                raise ValueError()
            start=datetime.fromisoformat(value['desde']);end=datetime.fromisoformat(value['hasta'])
            if start.utcoffset() is None or end.utcoffset() is None or not start<=(ahora or timezone.now())<end:
                raise ValueError()
            price=None if value['costoUnitarioCOP'] is None else Decimal(value['costoUnitarioCOP'])
            CondicionesConsultaControlada(entorno='PRODUCCION_CONTROLADA',costo_hdc=price,
                esquema_hdc=value['esquema'],referencia_costos=value['contratoReferencia'],
                referencia_entorno_y_scopes='PRESELECTA_API_INTERNA_HDC',
                referencia_no_duplicidad_remota='RESERVA_NATIVA_IDEMPOTENTE',
                referencia_almacenamiento_privado='RECIBO_NATIVO_CIFRADO',
                responsable_condiciones=value['responsable'],
                referencia_autorizacion_consumo=value['referenciaAprobacion'],vigente_hasta=end)
            if value['esquema']=='CARGO_VIGENTE_AUTORIZADO':raise ValueError()
        except (ValueError,TypeError,ArithmeticError):
            raise OperacionError('POLITICA_CONSUMO_VIGENCIA_O_COSTO_INVALIDO') from None
        return cls(json.loads(json.dumps(value)))

    def huella(self):
        import hashlib
        return hashlib.sha256(json.dumps(self.datos,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def cargar_politica_consumo():
    """Private administrable configuration; an absent policy never enables IO."""
    import os
    import stat
    from pathlib import Path
    try:
        path=Path(settings.CREDIHOY_HDC_POLITICA_FILE)
        if not path.is_absolute() or path.is_symlink():raise ValueError()
        fd=os.open(str(path),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size>8192
                    or os.name!='nt' and (info.st_uid!=os.geteuid() or info.st_mode&0o077)):
                raise ValueError()
            value=json.load(stream)
        return PoliticaConsumoInstitucional.desde_payload(value)
    except (OSError,ValueError,TypeError,AttributeError):
        raise OperacionError('POLITICA_CONSUMO_NO_CONFIGURADA') from None


def autorizar_consumo_por_politica(solicitud_id, *, operador):
    """One durable quota reservation and one native grant, before external IO."""
    from django.db import connection
    from django.utils import timezone
    from apps.utils.models import AuditLog
    from .centrales_personal import verificar_autorizacion_centrales_personal
    policy=cargar_politica_consumo();p=policy.datos
    if connection.vendor!='postgresql' or connection.in_atomic_block:
        raise OperacionError('POLITICA_REQUIERE_POSTGRES_AUTOCOMMIT')
    import hashlib
    lock=int(hashlib.sha256(('credihoy-cupo:'+p['id']).encode()).hexdigest()[:16],16)-2**63
    with transaction.atomic(durable=True):
        with connection.cursor() as cursor:cursor.execute('SELECT pg_advisory_xact_lock(%s)',[lock])
        # Reload under the quota lock; expiry or revocation never reuses a grant.
        if cargar_politica_consumo().huella()!=policy.huella():
            raise OperacionError('POLITICA_CAMBIO_REQUIERE_RELECTURA')
        case=_solicitud(solicitud_id,operador,autogestion=True)
        case=Solicitud.objects.select_for_update(of=('self',)).select_related('solicitante','consentimiento_consumo').get(pk=case.pk)
        verificar_autorizacion_centrales_personal(case,case.consentimiento_consumo)
        ledger=AuditLog.objects.filter(event='credihoy_cupo_hdc_reservado_v1',payload__politicaId=p['id'])
        existing=ledger.filter(solicitud=case).first()
        if existing:
            grant=case.metadatos.get('autorizacionEconomicaCredihoy',{})
            if grant.get('politicaSha256')!=policy.huella():
                raise OperacionError('POLITICA_CAMBIO_CON_RESERVA_NO_CONSUMIR')
            return {'autorizacionRegistrada':True,'reutilizada':True,'politicaSha256':policy.huella()}
        if OperacionGateway.objects.filter(solicitud=case,proveedor='HDC').exists():
            raise OperacionError('HDC_EXISTENTE_CONCILIAR_ANTES_DE_RESERVAR')
        if case.metadatos.get('autorizacionEconomicaCredihoy') is not None:
            raise OperacionError('AUTORIZACION_EXISTENTE_NO_SOBRESCRIBIR')
        day=timezone.now().date().isoformat()
        if ledger.count()>=p['cupoTotal'] or ledger.filter(payload__diaUTC=day).count()>=p['cupoDiario']:
            raise OperacionError('CUPO_INSTITUCIONAL_HDC_AGOTADO')
        price=None if p['costoUnitarioCOP'] is None else Decimal(p['costoUnitarioCOP'])
        conditions=CondicionesConsultaControlada(entorno='PRODUCCION_CONTROLADA',costo_hdc=price,
            esquema_hdc=p['esquema'],referencia_costos=p['contratoReferencia'],
            referencia_entorno_y_scopes='PRESELECTA_API_INTERNA_HDC',
            referencia_no_duplicidad_remota='RESERVA_NATIVA_IDEMPOTENTE',
            referencia_almacenamiento_privado='RECIBO_NATIVO_CIFRADO',
            responsable_condiciones=p['responsable'],referencia_autorizacion_consumo=p['referenciaAprobacion'],
            vigente_hasta=datetime.fromisoformat(p['hasta']))
        evidence={**conditions.evidencia(),'politicaId':p['id'],'politicaRevision':p['revision'],
            'politicaSha256':policy.huella(),'evidenciaComercialSha256':p['evidenciaComercialSha256']}
        case.metadatos={**case.metadatos,'consumoRealAutorizado':True,'autorizacionEconomicaCredihoy':evidence}
        case.save(update_fields=['metadatos','updated_at'])
        AuditLog.objects.create(solicitud=case,event='credihoy_cupo_hdc_reservado_v1',actor='user:'+str(operador.pk),
            request_id=p['id'],payload={'politicaId':p['id'],'politicaSha256':policy.huella(),
                'diaUTC':day,'condiciones':evidence,'consultasEjecutadas':0})
        return {'autorizacionRegistrada':True,'reutilizada':False,'politicaSha256':policy.huella()}


def validar_politica_del_consumo(case):
    grant=case.metadatos.get('autorizacionEconomicaCredihoy') or {}
    if not grant.get('politicaId'):return
    policy=cargar_politica_consumo()
    if not hmac.compare_digest(str(grant.get('politicaSha256','')),policy.huella()):
        raise OperacionError('POLITICA_CONSUMO_CAMBIO_O_REVOCACION')


def texto_consentimiento_controlado(condiciones=None):
    # Texto firmado CREDIHOY-REAL-1: inmutable, aunque el consumo se limite a HDC.
    texto = ("CONSULTA REAL CONTROLADA CREDIHOY UNICO. Autorizo una nueva consulta PRESELECTA "
        "y una nueva consulta HDC/Datacredito de mi propio expediente, para validar la evaluacion "
        "de CrediHoy Unico en Credito Digital Congente. La aceptacion se vincula a mi identidad, "
        "solicitud, fecha y OTP verificada. La autorizacion economica institucional es independiente: "
        "si esta pendiente, firmar no habilita las consultas. No autoriza reintentos "
        "automaticos, aprobacion crediticia ni radicacion LINIX. No reutiliza la autorizacion "
        "del ensayo historico. Las respuestas originales saneadas y normalizadas se vinculan "
        "al expediente privado de esta prueba, con acceso restringido a operadores autorizados "
        "y tratamiento conforme al alcance informado y su referencia de almacenamiento. "
        "Una falla de resultado o HTTP409 requiere conciliacion.")
    return texto if condiciones is None else texto + " Condiciones informadas: " + json.dumps(
        condiciones.evidencia(), sort_keys=True, ensure_ascii=True, separators=(",", ":"))


@dataclass(frozen=True, kw_only=True)
class CondicionesConsultaControlada:
    entorno: str
    costo_hdc: Decimal | None
    referencia_costos: str
    referencia_entorno_y_scopes: str
    referencia_no_duplicidad_remota: str
    referencia_almacenamiento_privado: str
    responsable_condiciones: str
    referencia_autorizacion_consumo: str
    esquema_hdc: str = "COSTO_UNITARIO"
    vigente_hasta: datetime | None = None

    def __post_init__(self):
        if (type(self.entorno) is not str or self.entorno not in {"PRODUCCION_CONTROLADA", "DEMO"}
                or (self.vigente_hasta is not None and (
                    type(self.vigente_hasta) is not datetime or self.vigente_hasta.utcoffset() is None))
                or any(type(esquema) is not str or esquema not in {"COSTO_UNITARIO", "CUPO_CONTRACTUAL", "COBERTURA_CONTRACTUAL", "CARGO_VIGENTE_AUTORIZADO"}
                    or (valor is None and esquema == "COSTO_UNITARIO")
                    or (valor is not None and esquema == "CARGO_VIGENTE_AUTORIZADO")
                    or (valor is not None and (type(valor) is not Decimal or not valor.is_finite() or valor < 0))
                    for valor, esquema in ((self.costo_hdc, self.esquema_hdc),))
                or any(type(v) is not str or not v.strip() or len(v) > 128 for v in (
                    self.referencia_costos, self.referencia_entorno_y_scopes,
                    self.referencia_no_duplicidad_remota, self.referencia_almacenamiento_privado,
                    self.responsable_condiciones, self.referencia_autorizacion_consumo))):
            raise ValueError("CONDICIONES_CONSUMO_REAL_NO_VERIFICADAS")

    def evidencia(self):
        evidencia = {"entorno": self.entorno,
            "costoHdcCOP": None if self.costo_hdc is None else str(self.costo_hdc), "referenciaCostos": self.referencia_costos,
            "esquemaHdc": self.esquema_hdc,
            "responsableCondiciones": self.responsable_condiciones,
            "referenciaAutorizacionConsumo": self.referencia_autorizacion_consumo,
            "referenciaEntornoScopes": self.referencia_entorno_y_scopes,
            "referenciaNoDuplicidadRemota": self.referencia_no_duplicidad_remota,
            "referenciaAlmacenamientoPrivado": self.referencia_almacenamiento_privado,
            "vigenteHasta": None if self.vigente_hasta is None else self.vigente_hasta.isoformat(),
            "vigencia": "UNA_EJECUCION_DE_LA_SOLICITUD",
            "proveedores": list(PROVEEDORES_CONSUMO), "maximoConsultasPorProveedor": 1,
            "reintentosAutomaticos": False, "autorizaDecisionCrediticia": False,
            "autorizaRadicacionLinix": False}
        if self.esquema_hdc == "CARGO_VIGENTE_AUTORIZADO":
            evidencia.update(tarifaCertificada=False, coberturaContractualCertificada=False,
                alcanceAutorizacionCargo="UNA_HDC_BAJO_CONDICIONES_COMERCIALES_VIGENTES")
        return evidencia


def _solicitud(solicitud_id, operador, *, autogestion=False):
    if autogestion:
        from .funcional import obtener_para_evaluacion
        return obtener_para_evaluacion(solicitud_id, operador, autogestion=True)
    if (not getattr(operador, "is_authenticated", False) or not operador.is_active or not operador.is_staff
            or getattr(settings, "CREDIHOY_CONSUMO_CONTROLADO_ENABLED", False) is not True):
        raise OperacionError("CONSUMO_CONTROLADO_NO_HABILITADO")
    s = Solicitud.objects.select_related("solicitante", "consentimiento_consumo__otp").get(pk=solicitud_id)
    if (s.asesor_id != operador.pk or s.producto != PRODUCTO
            or s.metadatos.get("modo") != "CONSULTA_REAL_CONTROLADA"):
        raise OperacionError("SOLICITUD_REAL_CONTROLADA_REQUERIDA")
    return s


def registrar_consentimiento_controlado(solicitud_id, *, operador, condiciones, aceptado, fecha):
    """Aceptacion NUEVA con OTP existente. No envia SMS ni crea OTP simulada."""
    if (type(condiciones) is not CondicionesConsultaControlada or aceptado is not True
            or type(fecha) is not datetime or fecha.utcoffset() is None):
        raise OperacionError("CONSENTIMIENTO_REAL_REQUERIDO")
    from django.utils import timezone
    if fecha > timezone.now() or (condiciones.vigente_hasta is not None and fecha > condiciones.vigente_hasta):
        raise OperacionError("FECHA_ACEPTACION_FUTURA")
    with transaction.atomic():
        s = _solicitud(solicitud_id, operador)
        Solicitud.objects.select_for_update().get(pk=s.pk)
        if ConsentimientoConsumo.objects.filter(solicitud=s).exists():
            raise OperacionError("CONSENTIMIENTO_EXISTENTE_NO_SOBRESCRIBIR")
        otp = s.active_otp_challenge
        if (otp is None or otp.estado != EstadoOtp.VALIDADA or otp.verificado_at is None
                or otp.verificado_at > fecha or not otp.transaction_uuid
                or not otp.provider or otp.provider.lower() in {"test", "mock", "local"}):
            raise OperacionError("OTP_REAL_VERIFICADA_REQUERIDA")
        texto = texto_consentimiento_controlado(condiciones)
        digest = build_consent_hash(VERSION_CONSENTIMIENTO, texto, otp.transaction_uuid, otp.canal, otp.verificado_at)
        return ConsentimientoConsumo.objects.create(solicitud=s, otp=otp, version=VERSION_CONSENTIMIENTO,
            aceptado=True, firmado=True, fecha_aceptacion=fecha, canal=otp.canal, text_snapshot=texto,
            text_hash=digest, tipo_firma="ACEPTACION_OTP", evidencia=condiciones.evidencia())


def registrar_autorizacion_consumo_controlado(solicitud_id, *, operador, condiciones, autorizado, autogestion=False):
    """Accion institucional explicita, independiente de la firma; no consulta proveedores."""
    from django.utils import timezone
    from .consentimiento_real import validar_documento
    if (autorizado is not True or type(condiciones) is not CondicionesConsultaControlada
            or (condiciones.vigente_hasta is not None and condiciones.vigente_hasta <= timezone.now())):
        raise OperacionError("AUTORIZACION_INSTITUCIONAL_CONSUMO_PENDIENTE")
    with transaction.atomic():
        s = _solicitud(solicitud_id, operador, autogestion=autogestion)
        s = Solicitud.objects.select_for_update().get(pk=s.pk)
        c = s.consentimiento_consumo
        if autogestion:
            from .centrales_personal import verificar_autorizacion_centrales_personal
            verificar_autorizacion_centrales_personal(s, c)
        else:
            validar_documento(s, c)
            if not c.firmado:
                raise OperacionError("CONSENTIMIENTO_REAL_FIRMADO_REQUERIDO")
        evidencia = condiciones.evidencia()
        existente = s.metadatos.get("autorizacionEconomicaCredihoy")
        if existente is not None and existente != evidencia:
            raise OperacionError("AUTORIZACION_EXISTENTE_NO_SOBRESCRIBIR")
        if existente is None:
            s.metadatos = {**s.metadatos, "autorizacionEconomicaCredihoy": evidencia, "consumoRealAutorizado": True}
            s.save(update_fields=["metadatos", "updated_at"])
            from apps.utils.models import AuditLog
            AuditLog.objects.create(solicitud=s, actor=f"user:{operador.pk}",
                event="credihoy_autorizacion_consumo", payload={"autorizado": True, "consultasEjecutadas": 0})
        return {"autorizacionRegistrada": True, "consultasEjecutadas": 0}


@dataclass(frozen=True, repr=False)
class PreparacionConsultaControlada:
    solicitud_id: str
    operaciones: tuple = field(repr=False)
    estado: str = "PENDIENTE_CONFIRMACION_ENVIO"

    def resumen(self):
        return {"solicitudId": self.solicitud_id, "estado": self.estado,
            "operaciones": [{"proveedor": o.proveedor, "operacionId": str(o.pk),
                "requestId": str(o.request_id), "estado": o.estado} for o in self.operaciones],
            "requiereConfirmacionFinal": True, "envioEjecutado": False,
            "decisionCrediticia": "NO_AUTORIZADA", "radicacionLinix": "NO_EJECUTADA"}


def preparar_consulta_controlada(solicitud_id, *, operador, hdc, configuracion,
                                 condiciones, reservar=False):
    """Valida exclusivamente HDC antes de reservar. Nunca envia consultas."""
    _enabled()
    if (type(configuracion) is not GatewayConfig or type(condiciones) is not CondicionesConsultaControlada
            or type(hdc) is not HDCRequest
            or type(reservar) is not bool):
        raise OperacionError("PREPARACION_REAL_INVALIDA")
    configuracion.validar()
    from django.utils import timezone
    if condiciones.vigente_hasta is not None and condiciones.vigente_hasta < timezone.now():
        raise OperacionError("CONSENTIMIENTO_REAL_VENCIDO")
    if not configuracion.verify_ssl:
        raise OperacionError("TLS_VERIFICADO_REQUERIDO")
    s = _solicitud(solicitud_id, operador)
    operaciones = []
    for proveedor, request in (("HDC", hdc),):
        if request.get("metadata") is None or request["metadata"].get("consumidor") != configuracion.internal_client:
            raise OperacionError("CONSUMIDOR_GATEWAY_INCONSISTENTE")
        consentimiento, identity, consent_hash, request_hash = validar(s, request)
        if consentimiento.evidencia.get("workflow") == "CREDIHOY_REAL_OTP_1":
            from .consentimiento_real import validar_documento
            validar_documento(s, consentimiento)
            if s.metadatos.get("autorizacionEconomicaCredihoy") != condiciones.evidencia():
                raise OperacionError("AUTORIZACION_INSTITUCIONAL_CONSUMO_PENDIENTE")
            if (not consentimiento.firmado or consentimiento.evidencia.get("estado") != "CONSENTIMIENTO_FIRMADO"
                    or consentimiento.otp.provider not in {"twilio_verify", "internal_email"}):
                raise OperacionError("CONSENTIMIENTO_ESPECIFICO_REAL_NO_VALIDO")
        otp = consentimiento.otp
        digest = build_consent_hash(consentimiento.version, consentimiento.text_snapshot,
            otp.transaction_uuid, otp.canal, otp.verificado_at)
        if (consentimiento.version != VERSION_CONSENTIMIENTO
                or consentimiento.text_snapshot != (texto_consentimiento_controlado()
                    if consentimiento.evidencia.get("workflow") == "CREDIHOY_REAL_OTP_1"
                    else texto_consentimiento_controlado(condiciones))
                or (consentimiento.evidencia.get("workflow") != "CREDIHOY_REAL_OTP_1"
                    and consentimiento.evidencia != condiciones.evidencia())
                or not hmac.compare_digest(consentimiento.text_hash, digest)
                or not otp.provider or otp.provider.lower() in {"test", "mock", "local"}):
            raise OperacionError("CONSENTIMIENTO_ESPECIFICO_REAL_NO_VALIDO")
        op = OperacionGateway.objects.filter(solicitud=s, proveedor=proveedor).first()
        if op is not None:
            OrquestadorGateway._compatible(op, consentimiento.pk, identity, consent_hash, request_hash)
            if op.estado != EstadoOperacion.PENDIENTE:
                raise OperacionError("CONSULTA_EXISTENTE_REQUIERE_RECUPERACION_O_CONCILIACION")
            operaciones.append(op)
    if reservar:
        # Reutiliza la reserva durable de CD2B; no crea otro mecanismo idempotente.
        orquestador = OrquestadorGateway()
        operaciones = [orquestador.preparar(s.pk, hdc)]
        if any(o.estado != EstadoOperacion.PENDIENTE for o in operaciones):
            raise OperacionError("CONSULTA_EXISTENTE_REQUIERE_RECUPERACION_O_CONCILIACION")
    estado = "PENDIENTE_CONFIRMACION_ENVIO" if len(operaciones) == 1 else "PENDIENTE_RESERVA_LOCAL"
    return PreparacionConsultaControlada(str(s.pk), tuple(operaciones), estado)


def descriptor_consentimiento_controlado(condiciones=None):
    """Borrador informativo: no acepta consentimiento ni inventa tarifas."""
    if condiciones is not None and type(condiciones) is not CondicionesConsultaControlada:
        raise OperacionError("CONDICIONES_CONSUMO_REAL_NO_VERIFICADAS")
    return {"version": VERSION_CONSENTIMIENTO, "texto": texto_consentimiento_controlado(condiciones),
        "finalidad": "ENSAYO_FUNCIONAL_CREDIHOY_UNICO", "proveedores": list(PROVEEDORES_CONSUMO),
        "identidad": "VINCULADA_A_SOLICITANTE_Y_OTP_VERIFICADA", "aceptado": False,
        "vigencia": "UNA_EJECUCION_DE_LA_SOLICITUD", "maximoConsultasPorProveedor": 1,
        "consultasRecurrentes": False, "costos": {"estado": "TARIFA_CONTRACTUAL_PENDIENTE"
            if condiciones is None else "INFORMADOS_CON_REFERENCIA",
            "hdcCOP": None if condiciones is None or condiciones.costo_hdc is None else str(condiciones.costo_hdc),
            "esquemaHdc": None if condiciones is None else condiciones.esquema_hdc},
        "aceptacionDisponible": condiciones is not None, "reintentosAutomaticos": False,
        "decisionCrediticia": "NO_AUTORIZADA", "radicacionLinix": "NO_EJECUTADA"}
