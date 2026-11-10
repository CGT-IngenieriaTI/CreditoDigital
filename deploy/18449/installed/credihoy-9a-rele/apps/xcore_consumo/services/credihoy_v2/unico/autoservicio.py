"""Existing owner contract: temporary progress, then explicit durable submission."""
from datetime import datetime
from decimal import Decimal
import re
from uuid import UUID
from django.conf import settings
from django.http import Http404
from django.utils import timezone
from django.db import connections
from rest_framework import serializers
from apps.solicitudes.models import Solicitud, EstadoSolicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.utils.titular import _normalize_colombia_mobile, _validate_primer_apellido
from apps.utils.validators import validate_document_issue_date
from .publico_institucional import PRODUCTO
from . import publico_institucional as wire

ORIGEN = 'AUTOGESTION_9A'
MODO = 'AUTOGESTION_ENVIADA'
VINCULO_CONSUMO = 'credihoy_autogestion_vinculo_v1'


def vincular_consumo_enviada(s, alias):
    """Same submitted dossier, no evaluation or credit consent inferred from 9A."""
    from apps.xcore_consumo.models import SolicitudConsumo, EstadoSolicitudConsumo
    if not connections[alias].in_atomic_block or s._state.db != alias:
        raise ValueError('VINCULO_CONSUMO_REQUIERE_TRANSACCION_ENVIO')
    meta = s.metadatos
    if (s.producto != PRODUCTO or meta.get('origen') != ORIGEN or meta.get('modo') != MODO
            or any(meta.get(k) != v for k, v in (
                ('productoLinix','390'),('modalidadLinix','111'),('destinacionLinix','CFA')))):
        raise ValueError('VINCULO_REQUIERE_ENVIO_AUTOGESTION_PROPIO')
    # The caller holds the holder lock; the one-to-one constraint also prevents duplicates.
    previous = SolicitudConsumo.objects.using(alias).filter(solicitud=s).first()
    if previous is not None:
        return previous
    if s.estado != EstadoSolicitud.INICIADA or s.paso_actual != 'enviada':
        raise ValueError('NO_CREAR_CONSUMO_EN_ANTECEDENTE_CERRADO')
    envio = meta['envio']
    values = envio['datos']
    amount = Decimal(str(s.monto_solicitado))
    if (str(UUID(envio['operacionId'])) != envio['operacionId']
            or not amount.is_finite() or amount <= 0
            or Decimal(str(values['monto'])) != amount
            or values['plazo'] != s.plazo_solicitado):
        raise ValueError('VINCULO_ENVIO_INCOMPATIBLE')
    detail, _ = SolicitudConsumo.objects.using(alias).get_or_create(solicitud=s, defaults={
        'estado':EstadoSolicitudConsumo.CONSENTIMIENTO_PENDIENTE,
        'form_data':{'monto':format(amount,'.2f'),'plazoMeses':s.plazo_solicitado},
        'orchestration_data':{VINCULO_CONSUMO:{'version':1,'solicitudId':str(s.pk),
            'origen':ORIGEN,'modo':MODO,'producto':'390','modalidad':'111','destinacion':'CFA',
            'operacionEnvioOriginal':envio['operacionId'],'creadoDesdeEnvioEn':s.created_at.isoformat(),
            'aceptacionReferencia':'metadatos.envio.aceptacionTemporal'}}})
    return detail


def habilitar(sesion=None, *, expira_at=None):
    if getattr(settings, 'CREDIHOY_AUTOSERVICIO_ENABLED', False) is not True:
        raise wire.NoDisponible
    if getattr(settings, 'CREDIHOY_PUBLICO_RESTRINGIDO', False) is True:
        try:
            scope = settings.CREDIHOY_AUTOSERVICIO_ALCANCE
            if type(scope) is dict and scope.get('modo')=='SESION_PERSONAL_VERIFICADA':
                from .publico_controles import admision_ensayo_vigente
                if set(scope)!={'modo','hasta'} or not admision_ensayo_vigente():raise ValueError
                if type(sesion) is not str or str(UUID(sesion))!=sesion:raise ValueError
                expiry=datetime.fromisoformat(expira_at) if type(expira_at) is str else expira_at
                until=datetime.fromisoformat(scope['hasta'])
                window=datetime.fromisoformat(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA)
                now=timezone.now()
                if (not isinstance(expiry,datetime) or not timezone.is_aware(expiry)
                        or not timezone.is_aware(until) or not timezone.is_aware(window)
                        or not now<expiry<=now+__import__('datetime').timedelta(minutes=15)
                        or not now<until<=window):raise ValueError
                return
            if (type(scope) is not dict or set(scope) != {'sesiones','hasta'}
                    or type(scope['sesiones']) is not list or len(scope['sesiones']) != 1
                    or type(sesion) is not str or str(UUID(sesion)) != sesion
                    or scope['sesiones'] != [sesion]):raise ValueError
            until = datetime.fromisoformat(scope['hasta'])
            window = datetime.fromisoformat(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA)
            now = timezone.now()
            if (not timezone.is_aware(until) or not timezone.is_aware(window)
                    or not now < until <= window):raise ValueError
        except (AttributeError,KeyError,TypeError,ValueError):
            raise wire.NoDisponible from None


def sesion_habilitada(sesion, *, expira_at=None):
    try:habilitar(sesion,expira_at=expira_at)
    except wire.NoDisponible:return False
    return True


def leer_enviadas(identity, payload):
    """Owner-filtered durable submissions, independent of historical views/rules."""
    from django.db import transaction
    from .progreso_temporal import alias_operativo, proteger_conexion
    if not sesion_habilitada(identity.get('_sesionReferencia')):return []
    try:
        expires=datetime.fromisoformat(identity['_sesionExpiraAt'])
        if not timezone.is_aware(expires) or expires<=timezone.now():raise ValueError
    except (KeyError,TypeError,ValueError):raise wire.NoDisponible from None
    alias = alias_operativo(sesion=identity.get('_sesionReferencia'))
    with transaction.atomic(using=alias):
        with connections[alias].cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
        proteger_conexion(alias)
        query = Solicitud.objects.using(alias).filter(producto=PRODUCTO,
            metadatos__origen=ORIGEN,metadatos__modo=MODO,
            solicitante__tipo_identificacion='CC',
            solicitante__numero_identificacion=identity['numeroDocumento'])
        if 'solicitudId' in payload:query=query.filter(pk=UUID(payload['solicitudId']))
        rows=[]
        for s in query.order_by('-created_at','pk')[:20]:
            rows.append(wire.project_row({'referencia':str(s.pk),'titulo':'Tu solicitud esta en revision',
                'monto':str(s.monto_solicitado),'plazoMeses':s.plazo_solicitado,'periodicidad':'Mensual',
                'fechaCreacion':s.created_at.isoformat(),'requisitosCumplidos':None,
                'pendientes':['Necesitamos completar algunas validaciones antes de tomar una decision.'],
                'siguientePaso':'Espera la revision de Congente.','ofertaDisponible':False,
                'acciones':{'radicar':False,'firmar':False},'institucional':None,'simulacion':None,
                'procedencia':{'origen':'FUENTE_OPERATIVA','estadoRegistrado':s.get_estado_display(),
                    'actualizadoAt':s.updated_at.isoformat(),'consultadoAt':timezone.now().isoformat()}}))
    return rows


def validar_promotor(value):
    if value is None:return None
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value):raise ValueError('PROMOTOR_INVALIDO')
    catalog = getattr(settings, 'CREDIHOY_PROMOTORES_CATALOGO', None)
    if type(catalog) is not dict or set(catalog) != {'referencia', 'observadoEn', 'vigenteHasta', 'promotores'}:
        raise wire.NoDisponible
    try:
        observed, until = datetime.fromisoformat(catalog['observadoEn']), datetime.fromisoformat(catalog['vigenteHasta'])
        if (not catalog['referencia'] or not timezone.is_aware(observed) or not timezone.is_aware(until)
                or not observed <= timezone.now() < until):raise ValueError
        row = catalog['promotores'][value]
        if type(row) is not dict or row.get('activo') is not True:raise ValueError
    except (KeyError, TypeError, ValueError):raise ValueError('PROMOTOR_INACTIVO_O_INEXISTENTE') from None
    return {'referencia':value,'catalogoReferencia':catalog['referencia'],'catalogoObservadoEn':catalog['observadoEn']}


def proyectar(s):
    state = s.metadatos['envio']
    return {'referencia':str(s.pk),'fase':'ENVIADA','version':state['version'],
        'datos':state['datos'],'promotor':state['promotor']['referencia'] if state['promotor'] else None,
        'actualizadoAt':s.updated_at.isoformat(),'venceAt':None,
        'acciones':{'radicar':False,'firmar':False},'solicitudRegistrada':True}


def enviada(identity, alias, key=None):
    query = Solicitud.objects.using(alias).filter(producto=PRODUCTO,metadatos__origen=ORIGEN,metadatos__modo=MODO)
    if key:
        candidate = Solicitud.objects.using(alias).filter(pk=key).select_related('solicitante').first()
        if candidate and (candidate.solicitante.numero_identificacion != identity['numeroDocumento']
                or candidate.metadatos.get('modo') != MODO or candidate.producto != PRODUCTO):raise Http404
        query = query.filter(pk=key)
    s = query.filter(solicitante__tipo_identificacion='CC',
        solicitante__numero_identificacion=identity['numeroDocumento']).order_by('-created_at').first()
    return s


def registrar_enviada(identity, alias, key, version, values, promoter, acceptance, operation, digest):
    """Only called inside the progress transaction after explicit, authenticated send."""
    if not connections[alias].in_atomic_block:
        raise ValueError('ENVIO_REQUIERE_TRANSACCION_PROGRESO')
    if not {'monto','plazo'} <= set(values):raise ValueError('SOLICITUD_INCOMPLETA')
    applicant = Solicitante.objects.using(alias).filter(tipo_identificacion='CC',
        numero_identificacion=identity['numeroDocumento']).first()
    if not applicant:
        surname = _validate_primer_apellido(identity.get('primerApellido',''))
        issued = serializers.DateField(validators=[validate_document_issue_date]).run_validation(identity.get('fechaExpedicion'))
        phone = _normalize_colombia_mobile(identity.get('contacto','') if identity.get('canal') == 'SMS' else identity.get('celular',''))
        applicant = Solicitante.objects.using(alias).create(tipo_identificacion='CC',
            numero_identificacion=identity['numeroDocumento'],primer_apellido=surname,fecha_expedicion=issued,
            celular=phone,email=values.get('email',identity.get('correo') or ''))
    s = Solicitud(id=key,solicitante=applicant,asesor=None,producto=PRODUCTO,estado=EstadoSolicitud.INICIADA,
        paso_actual='enviada',monto_solicitado=values['monto'],plazo_solicitado=values['plazo'],
        metadatos={'origen':ORIGEN,'modo':MODO,'productoLinix':'390','modalidadLinix':'111','destinacionLinix':'CFA',
            'envio':{'version':version,'datos':values,'promotor':promoter,'aceptacionTemporal':acceptance,
                'operacionId':str(operation),'peticionHmac':digest}})
    s.save(using=alias,force_insert=True)
    AuditLog.objects.using(alias).create(solicitud=s,event='credihoy_solicitud_enviada',actor='titular:9A',
        request_id=str(operation),payload={'origen':ORIGEN,'version':version,'solicitudRegistrada':True,
            'avisoVersion':acceptance['version'],'avisoSha256':acceptance['sha256']})
    vincular_consumo_enviada(s,alias)
    return s


def operar(identity, payload):
    from .progreso_temporal import operar as temporal
    return temporal(identity,payload)
