from pathlib import Path
import hashlib
import json

from .models import AceptacionDocumento, DocumentoLegal, TipoDocumento
from .models import EventoDocumental, VersionDocumental


DEFAULT_DOCUMENTS = [
    {
        "codigo": "centrales-riesgo",
        "tipo_documento": TipoDocumento.CENTRALES,
        "titulo": "Autorizacion de consulta a centrales de riesgo",
        "descripcion": "Autorizacion para consultar informacion crediticia y financiera.",
        "orden": 1,
    },
]


def ensure_default_documents():
    for item in DEFAULT_DOCUMENTS:
        DocumentoLegal.objects.get_or_create(
            codigo=item["codigo"],
            defaults={**item, "activo": True},
        )


def get_active_documents():
    ensure_default_documents()
    return DocumentoLegal.objects.filter(codigo="centrales-riesgo", activo=True).order_by("orden", "titulo")


def get_document_asset_path(codigo: str) -> Path | None:
    if codigo == "centrales-riesgo":
        return Path(__file__).resolve().parents[1] / "xcore_consumo" / "assets" / "autorizacion_centrales_riesgo.pdf"
    return None


def _hash_evidencia(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def preparar_version_documental(documento):
    """Text-only presentation contract; not a PDF, signature or acceptance."""
    data={key:getattr(documento,key) for key in ('id','codigo','tipo_documento','titulo','descripcion','version')}
    data['representacion']='TEXTO_DOCUMENTOLEGAL_NO_PDF'
    return {'documento':data,'sha256':_hash_evidencia(data),'registraAceptacion':False}


def registrar_aceptacion_versionada(solicitud_id, documento_id, *, actor, version_presentada,
        sha256_presentado, aceptado, ahora=None):
    """Explicit internal operation, not wired to public or credit-consent endpoints."""
    from django.core.exceptions import PermissionDenied, ValidationError
    from django.db import transaction
    from django.utils import timezone
    from apps.solicitudes.models import Solicitud
    from apps.utils.models import AuditLog
    if (not actor.is_authenticated or not actor.is_active or not actor.is_staff
            or not actor.has_perm('solicitudes.view_solicitud')
            or not actor.has_perm('documentos.add_aceptaciondocumento')):
        raise PermissionDenied
    ahora=ahora or timezone.now()
    if type(aceptado) is not bool or not timezone.is_aware(ahora) or ahora>timezone.now():
        raise ValidationError('ACEPTACION_O_FECHA_INVALIDA')
    with transaction.atomic():
        cases=Solicitud.objects.select_for_update()
        if not actor.is_superuser:
            cases=cases.filter(asesor=actor)
        try:
            case=cases.get(pk=solicitud_id)
        except Solicitud.DoesNotExist:
            raise PermissionDenied from None
        document=DocumentoLegal.objects.select_for_update().get(pk=documento_id,activo=True)
        shown=preparar_version_documental(document)
        if version_presentada!=document.version or sha256_presentado!=shown['sha256']:
            raise ValidationError('VERSION_PRESENTADA_NO_COINCIDE_CON_DOCUMENTO')
        previous=AceptacionDocumento.objects.filter(solicitud=case,documento=document).first()
        if previous:
            if (previous.evidencia_version and previous.aceptado is aceptado
                    and previous.evidencia_version.get('documentoSha256')==shown['sha256']
                    and _version_registrada(previous,ahora) is not None):
                return previous
            raise ValidationError('ACEPTACION_EXISTENTE_NO_RECONSTRUIR_NI_SOBRESCRIBIR')
        evidence={'schema':'aceptacion-texto-documental-v1','solicitudId':str(case.pk),
            'documentoId':document.pk,'documento':shown['documento'],'documentoSha256':shown['sha256'],
            'aceptado':aceptado,'fecha':ahora.isoformat(),'actorId':actor.pk}
        evidence['sha256']=_hash_evidencia(evidence)
        result=AceptacionDocumento.objects.create(solicitud=case,documento=document,aceptado=aceptado,
            fecha_aceptacion=ahora,evidencia_version=evidence)
        AuditLog.objects.create(solicitud=case,event='documento_aceptacion_versionada',
            actor=f'user:{actor.pk}',payload={'documentoId':document.pk,'sha256':evidence['sha256'],
                'version':document.version,'firmaContractual':False})
        return result


def _version_registrada(item, ahora):
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime
    evidence=item.evidencia_version
    if not isinstance(evidence,dict):
        return None
    try:
        body={key:value for key,value in evidence.items() if key!='sha256'}
        date=parse_datetime(evidence['fecha'])
        document=evidence['documento']
        if (evidence['schema']!='aceptacion-texto-documental-v1'
                or evidence['solicitudId']!=str(item.solicitud_id)
                or evidence['documentoId']!=item.documento_id or document['id']!=item.documento_id
                or evidence['aceptado'] is not item.aceptado
                or date is None or not timezone.is_aware(date) or date!=item.fecha_aceptacion or date>ahora
                or document['representacion']!='TEXTO_DOCUMENTOLEGAL_NO_PDF'
                or evidence['documentoSha256']!=_hash_evidencia(document)
                or evidence['sha256']!=_hash_evidencia(body)
                or not isinstance(document['version'],str) or not document['version']):
            return None
        return document['version']
    except (KeyError,TypeError,ValueError):
        return None


def leer_evidencia_documental(solicitud_id, *, ahora):
    """Inventory only: mutable legal text is not proof of the accepted version."""
    from django.utils import timezone
    if not timezone.is_aware(ahora):
        raise ValueError('FECHA_CORTE_DOCUMENTAL_SIN_ZONA')
    rows = []
    for item in AceptacionDocumento.objects.filter(solicitud_id=solicitud_id).select_related('documento'):
        dated = (timezone.is_aware(item.fecha_aceptacion) and item.fecha_aceptacion <= ahora)
        version=_version_registrada(item,ahora)
        rows.append({'documentoId': item.documento_id, 'codigo': item.documento.codigo,
            'tipo': item.documento.tipo_documento, 'documentoActivo': item.documento.activo,
            'versionActualDocumento': item.documento.version,
            'versionPresentadaVerificada': version,
            'versionAceptada': version if item.aceptado else None,
            'integridadVersion': 'VERIFICADA' if version is not None else
                'LEGADO_SIN_SNAPSHOT' if item.evidencia_version is None else 'NO_ACREDITADA',
            'alcanceVersion': 'TEXTO_DOCUMENTOLEGAL_NO_PDF' if version is not None else None,
            'fechaAceptacion': item.fecha_aceptacion.isoformat(),
            'estado': 'FECHA_NO_VALIDA' if not dated else 'REGISTRADA' if item.aceptado else 'NO_ACEPTADA',
            'visualizacionSegundos': item.visualizacion_segundos, 'llegoAlFinal': item.llego_al_final})
    return {'fuente': 'DOCUMENTOS_ACEPTACIONES_PERSISTIDAS', 'soloLectura': True,
        'fechaCorte': ahora.isoformat(), 'aceptaciones': rows,
        'versionAceptadaDisponible': bool(rows) and all(row['versionAceptada'] is not None for row in rows),
        'aceptacionTitularAutenticada': False, 'firmaContratoAcreditada': False,
        'limitacion': ('EVIDENCIA_TEXTO_NO_CERTIFICA_PDF_NI_FIRMA_CONTRACTUAL'
            if rows and all(row['integridadVersion']=='VERIFICADA' for row in rows)
            else 'ACEPTACION_SIN_SNAPSHOT_DE_VERSION_NO_CERTIFICA_FIRMA_CONTRACTUAL')}


def _permiso_documental(actor, permiso):
    from django.core.exceptions import PermissionDenied
    if (not actor or not actor.is_authenticated or not actor.is_active or not actor.is_staff
            or not actor.has_perm('solicitudes.view_solicitud')
            or not actor.has_perm('documentos.' + permiso)):
        raise PermissionDenied


def _expediente_documental(solicitud_id, actor, *, bloquear=False):
    from django.core.exceptions import PermissionDenied
    from apps.solicitudes.models import Solicitud
    query = Solicitud.objects.all()
    if bloquear:
        query = query.select_for_update()
    if not actor.is_superuser:
        query = query.filter(asesor=actor)
    try:
        return query.get(pk=solicitud_id)
    except Solicitud.DoesNotExist:
        raise PermissionDenied from None


def _contexto_documental(case, contexto):
    from django.core.exceptions import ValidationError
    from django.core.serializers.json import DjangoJSONEncoder
    keys = {'proposito', 'etapa', 'revisionExpediente', 'versionPolitica'}
    if (not isinstance(contexto, dict) or not contexto or not contexto.get('proposito')
            or not set(contexto) <= keys
            or any(not isinstance(v, str) or not v.strip() or len(v) > 120 for v in contexto.values())):
        raise ValidationError('CONTEXTO_DOCUMENTAL_INVALIDO')
    # Bind to the stored case, not an arbitrary UUID or context supplied by a caller.
    data = {k: getattr(case, k) for k in ('id', 'solicitante_id', 'asesor_id', 'producto',
        'monto_solicitado', 'plazo_solicitado', 'estado', 'metadatos', 'updated_at')}
    normalized = json.loads(json.dumps(data, cls=DjangoJSONEncoder))
    return {'declarado': dict(contexto), 'expedienteSha256': _hash_evidencia(normalized)}


def autorizar_version_documental(documento_id, *, actor, referencia, vigente_desde, vigente_hasta):
    """Register an explicit supplied authorization reference, never infer legal approval."""
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.utils import timezone
    from datetime import datetime
    _permiso_documental(actor, 'add_versiondocumental')
    if (not isinstance(referencia, str) or not referencia.strip() or len(referencia) > 200
            or not isinstance(vigente_desde, datetime) or not isinstance(vigente_hasta, datetime)
            or not timezone.is_aware(vigente_desde) or not timezone.is_aware(vigente_hasta)
            or vigente_desde >= vigente_hasta):
        raise ValidationError('AUTORIZACION_DOCUMENTAL_INVALIDA')
    with transaction.atomic():
        document = DocumentoLegal.objects.select_for_update().get(pk=documento_id, activo=True)
        shown = preparar_version_documental(document)
        body = {'schema': 'version-documental-v1', 'documento': shown['documento'],
            'documentoSha256': shown['sha256'], 'referenciaAutorizacion': referencia.strip(),
            'vigenteDesde': vigente_desde.isoformat(), 'vigenteHasta': vigente_hasta.isoformat(),
            'actorId': actor.pk, 'alcance': 'PRESENTACION_TEXTO_NO_FIRMA'}
        existing = VersionDocumental.objects.filter(documento=document, version=document.version).first()
        if existing:
            if existing.sha256 != _hash_evidencia(body) or existing.evidencia != body:
                raise ValidationError('VERSION_DOCUMENTAL_YA_SELLADA')
            return existing
        result = VersionDocumental.objects.create(documento=document, version=document.version,
            actor=actor, evidencia=body, sha256=_hash_evidencia(body))
        _auditar_documento(None, actor, 'documento_version_autorizada', result)
        return result


def _auditar_documento(case, actor, event, evidence):
    from apps.utils.models import AuditLog
    AuditLog.objects.create(solicitud=case, event=event, actor=f'user:{actor.pk}',
        payload={'referencia': str(evidence.pk), 'sha256': evidence.sha256,
            'aceptacionTitularAutenticada': False, 'firmaContratoAcreditada': False})


def _verificar_version(version, document, now):
    from django.core.exceptions import ValidationError
    from django.utils.dateparse import parse_datetime
    body = version.evidencia
    valid = _version_integra(version)
    if valid:
        start, end = (parse_datetime(body[k]) for k in ('vigenteDesde', 'vigenteHasta'))
        shown = preparar_version_documental(document)
        valid = (body['documento'] == shown['documento'] and body['documentoSha256'] == shown['sha256']
            and version.version == document.version
            and version.documento_id == document.pk and document.activo
            and start <= now <= end)
    if not valid:
        raise ValidationError('VERSION_DOCUMENTAL_NO_VIGENTE_O_MODIFICADA')


def _version_integra(version):
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime
    body = version.evidencia
    try:
        start, end = (parse_datetime(body[k]) for k in ('vigenteDesde', 'vigenteHasta'))
        return (body['schema'] == 'version-documental-v1'
            and version.sha256 == _hash_evidencia(body) and body['actorId'] == version.actor_id
            and body['documento']['id'] == version.documento_id
            and body['documento']['version'] == version.version
            and body['documento']['representacion'] == 'TEXTO_DOCUMENTOLEGAL_NO_PDF'
            and body['documentoSha256'] == _hash_evidencia(body['documento'])
            and body['alcance'] == 'PRESENTACION_TEXTO_NO_FIRMA'
            and isinstance(body['referenciaAutorizacion'], str) and bool(body['referenciaAutorizacion'].strip())
            and start is not None and end is not None and timezone.is_aware(start)
            and timezone.is_aware(end) and start < end)
    except (KeyError, TypeError, ValueError):
        return False


def _clave_documental(value):
    import uuid
    from django.core.exceptions import ValidationError
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('CLAVE_DOCUMENTAL_INVALIDA') from None


def _evento_integro(event):
    body = event.evidencia
    return (isinstance(body, dict) and body.get('schema') == 'evento-documental-v1'
        and body.get('aceptacionTitularAutenticada') is False
        and body.get('firmaContratoAcreditada') is False
        and isinstance(body.get('contexto'), dict) and event.sha256 == _hash_evidencia(body)
        and body.get('eventoId') == str(event.pk) and body.get('solicitudId') == str(event.solicitud_id)
        and body.get('versionId') == str(event.version_documental_id)
        and body.get('actorId') == event.actor_id and body.get('tipo') == event.tipo
        and body.get('claveIdempotencia') == str(event.clave_idempotencia)
        and body.get('presentacionId') == (str(event.presentacion_id) if event.presentacion_id else None))


def _registrar_evento(case, version, actor, key, tipo, contexto, parent=None):
    import uuid
    identifier = uuid.uuid4()
    body = {'schema': 'evento-documental-v1', 'eventoId': str(identifier),
        'solicitudId': str(case.pk), 'versionId': str(version.pk), 'versionSha256': version.sha256,
        'tipo': tipo, 'contexto': contexto, 'actorId': actor.pk, 'claveIdempotencia': str(key),
        'presentacionId': str(parent.pk) if parent else None,
        'presentacionSha256': parent.sha256 if parent else None,
        'aceptacionTitularAutenticada': False, 'firmaContratoAcreditada': False}
    event = EventoDocumental.objects.create(id=identifier, solicitud=case, version_documental=version,
        actor=actor, clave_idempotencia=key, tipo=tipo, presentacion=parent,
        evidencia=body, sha256=_hash_evidencia(body))
    _auditar_documento(case, actor, 'documento_' + tipo.lower(), event)
    return event


def presentar_documento(solicitud_id, documento_id, *, actor, contexto, clave_idempotencia):
    """Internal presentation. No public acceptance/signature route is enabled."""
    from django.core.exceptions import ValidationError
    from django.db import transaction
    from django.utils import timezone
    _permiso_documental(actor, 'add_eventodocumental')
    key = _clave_documental(clave_idempotencia)
    with transaction.atomic():
        case = _expediente_documental(solicitud_id, actor, bloquear=True)
        context = _contexto_documental(case, contexto)
        existing = EventoDocumental.objects.select_related('version_documental').filter(
            solicitud=case, clave_idempotencia=key).first()
        if existing:
            if (existing.tipo != EventoDocumental.Tipo.PRESENTACION or not _evento_integro(existing)
                    or existing.actor_id != actor.pk or existing.evidencia['contexto'] != context
                    or existing.version_documental.documento_id != documento_id):
                raise ValidationError('IDEMPOTENCIA_DOCUMENTAL_EN_CONFLICTO')
            return existing
        document = DocumentoLegal.objects.select_for_update().get(pk=documento_id, activo=True)
        try:
            version = VersionDocumental.objects.get(documento=document, version=document.version)
        except VersionDocumental.DoesNotExist:
            raise ValidationError('VERSION_SIN_AUTORIZACION_DE_PRESENTACION') from None
        _verificar_version(version, document, timezone.now())
        return _registrar_evento(case, version, actor, key, EventoDocumental.Tipo.PRESENTACION, context)


def recibir_respuesta_documental(solicitud_id, presentacion_id, *, actor, contexto,
        clave_idempotencia, sha256_presentacion, aceptado):
    """Administrative declaration only. Never creates AceptacionDocumento or a signature."""
    from django.core.exceptions import PermissionDenied, ValidationError
    from django.db import transaction
    from django.utils import timezone
    _permiso_documental(actor, 'add_eventodocumental')
    if type(aceptado) is not bool:
        raise ValidationError('RESPUESTA_DOCUMENTAL_INVALIDA')
    key = _clave_documental(clave_idempotencia)
    kind = EventoDocumental.Tipo.ACEPTACION_ADMIN if aceptado else EventoDocumental.Tipo.NEGATIVA_ADMIN
    with transaction.atomic():
        case = _expediente_documental(solicitud_id, actor, bloquear=True)
        try:
            parent = EventoDocumental.objects.select_related('version_documental').get(
                pk=presentacion_id, solicitud=case, tipo=EventoDocumental.Tipo.PRESENTACION)
        except EventoDocumental.DoesNotExist:
            raise PermissionDenied from None
        context = _contexto_documental(case, contexto)
        if (not _evento_integro(parent) or parent.sha256 != sha256_presentacion
                or parent.evidencia['contexto'] != context
                or parent.evidencia['versionSha256'] != parent.version_documental.sha256):
            raise ValidationError('PRESENTACION_O_CONTEXTO_NO_COINCIDE')
        existing = EventoDocumental.objects.filter(solicitud=case, clave_idempotencia=key).first()
        if existing:
            if (not _evento_integro(existing) or existing.presentacion_id != parent.pk
                    or existing.tipo != kind or existing.actor_id != actor.pk):
                raise ValidationError('IDEMPOTENCIA_DOCUMENTAL_EN_CONFLICTO')
            return existing
        if parent.respuestas.exists():
            raise ValidationError('PRESENTACION_YA_TIENE_RESPUESTA')
        document = DocumentoLegal.objects.select_for_update().get(pk=parent.version_documental.documento_id)
        _verificar_version(parent.version_documental, document, timezone.now())
        return _registrar_evento(case, parent.version_documental, actor, key, kind, context, parent)


def contrato_presentacion_documental(solicitud_id, presentacion_id, *, actor):
    """Return exactly what was shown, not the current editable legal document."""
    from django.core.exceptions import PermissionDenied, ValidationError
    _permiso_documental(actor, 'view_eventodocumental')
    _expediente_documental(solicitud_id, actor)
    try:
        event = EventoDocumental.objects.select_related('version_documental').get(pk=presentacion_id,
            solicitud_id=solicitud_id, tipo=EventoDocumental.Tipo.PRESENTACION)
    except EventoDocumental.DoesNotExist:
        raise PermissionDenied from None
    version = event.version_documental
    if (not _evento_integro(event) or not _version_integra(version)
            or event.evidencia['versionSha256'] != version.sha256):
        raise ValidationError('EVIDENCIA_DOCUMENTAL_INCONSISTENTE')
    return {'presentacionId': str(event.pk), 'solicitudId': str(event.solicitud_id),
        'fechaPresentacion': event.created_at.isoformat(), 'sha256Presentacion': event.sha256,
        'versionReferencia': str(version.pk), 'documento': version.evidencia['documento'],
        'documentoSha256': version.evidencia['documentoSha256'],
        'referenciaAutorizacion': version.evidencia['referenciaAutorizacion'],
        'vigenteDesde': version.evidencia['vigenteDesde'], 'vigenteHasta': version.evidencia['vigenteHasta'],
        'contexto': event.evidencia['contexto'], 'aceptacionTitularAutenticada': False,
        'firmaContratoAcreditada': False, 'recepcionRealHabilitada': False}


def leer_historial_documental(solicitud_id, *, actor):
    from django.db.models import Case, IntegerField, Value, When
    from django.utils import timezone
    _permiso_documental(actor, 'view_eventodocumental')
    _expediente_documental(solicitud_id, actor)
    rows = []
    # UUID order must not place a response before its presentation at the same instant.
    causal_order = Case(When(tipo=EventoDocumental.Tipo.PRESENTACION, then=Value(0)),
        default=Value(1), output_field=IntegerField())
    for event in EventoDocumental.objects.filter(solicitud_id=solicitud_id).select_related(
            'version_documental', 'version_documental__documento', 'presentacion').order_by(
                'created_at', causal_order, 'id'):
        version = event.version_documental
        valid = (_evento_integro(event) and _version_integra(version)
            and event.evidencia.get('versionSha256') == version.sha256)
        if event.presentacion_id:
            valid = (valid and _evento_integro(event.presentacion)
                and event.presentacion.tipo == EventoDocumental.Tipo.PRESENTACION
                and event.presentacion.solicitud_id == event.solicitud_id
                and event.presentacion.version_documental_id == version.pk
                and event.evidencia.get('presentacionSha256') == event.presentacion.sha256)
        rows.append({'eventoId': str(event.pk), 'tipo': event.tipo,
            'documento': version.documento.codigo, 'version': version.version,
            'fecha': event.created_at.isoformat(), 'sha256': event.sha256,
            'integridad': 'VERIFICADA' if valid else 'NO_ACREDITADA'})
    return {'eventos': rows, 'historico': leer_evidencia_documental(solicitud_id, ahora=timezone.now()),
        'aceptacionTitularAutenticada': False, 'firmaContratoAcreditada': False,
        'recepcionRealHabilitada': False}
