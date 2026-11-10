"""Preparacion local Autentic en 9H. Nunca envia ni acredita firmas o consentimiento."""
from datetime import datetime
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import PlanPaqueteDocumental, VersionDocumental
from .services import (_auditar_documento, _clave_documental, _contexto_documental,
    _expediente_documental, _hash_evidencia, _permiso_documental, _verificar_version)


ETAPAS = {'PRECONSULTA', 'PRERADICACION', 'FORMALIZACION'}
TIPOS = {'AUTORIZACION_CENTRALES', 'SOLICITUD_CREDITO', 'FORMALIZACION'}
PRECONDICIONES = {'CONTENIDO_DEFINITIVO', 'IDENTIDAD_ACREDITADA',
    'HOMOLOGACION_JURIDICA', 'ANTES_CONSULTA_CENTRALES', 'ANTES_RADICACION',
    'APROBACION_INSTITUCIONAL'}


def _documentos(config, ahora):
    if (type(config) is not dict or set(config) != {'perfil', 'proveedor', 'documentos'}
            or config['perfil'] != {'producto': '390', 'modalidad': '111', 'destinacion': 'CFA'}
            or config['proveedor'] != 'AUTENTIC'
            or type(config['documentos']) is not list or not 1 <= len(config['documentos']) <= 5):
        raise ValidationError('CONFIGURACION_PAQUETE_INVALIDA')
    rows, ids, orders = [], set(), set()
    for item in config['documentos']:
        if (type(item) is not dict or set(item) != {'versionId', 'tipo', 'etapa',
                'firmaRequerida', 'orden', 'precondiciones'}
                or type(item['tipo']) is not str or item['tipo'] not in TIPOS
                or type(item['etapa']) is not str or item['etapa'] not in ETAPAS
                or (item['firmaRequerida'] is not None and type(item['firmaRequerida']) is not bool)
                or type(item['orden']) is not int or not 1 <= item['orden'] <= 5
                or item['orden'] in orders or type(item['precondiciones']) is not list
                or any(type(p) is not str or p not in PRECONDICIONES for p in item['precondiciones'])
                or len(item['precondiciones']) != len(set(item['precondiciones']))):
            raise ValidationError('DOCUMENTO_PAQUETE_INVALIDO')
        identifier = _clave_documental(item['versionId'])
        if identifier in ids:
            raise ValidationError('DOCUMENTO_PAQUETE_DUPLICADO')
        version = VersionDocumental.objects.select_related('documento').get(pk=identifier)
        # Serialize catalog edits with preparation, using the same lock as 9H.
        document = type(version.documento).objects.select_for_update().get(pk=version.documento_id)
        _verificar_version(version, document, ahora)
        if any(word in document.codigo.lower() for word in ('pagare', 'linpag')):
            raise ValidationError('PAGARE_EXCLUSIVO_LINPAG')
        ids.add(identifier); orders.add(item['orden'])
        rows.append(dict(item, versionId=str(identifier), versionSha256=version.sha256,
            documentoSha256=version.evidencia['documentoSha256'], codigoDocumento=document.codigo,
            vigenteDesde=version.evidencia['vigenteDesde'], vigenteHasta=version.evidencia['vigenteHasta'],
            alcanceVersion=version.evidencia['alcance']))
    if len({r['etapa'] for r in rows}) != 1:
        raise ValidationError('ETAPAS_DISTINTAS_REQUIEREN_PAQUETES_SEPARADOS')
    return sorted(rows, key=lambda r: r['orden'])


def preparar_paquete(solicitud_id, *, actor, codigo, version, configuracion, contexto,
        clave_idempotencia, ahora=None):
    """Sella una propuesta con versiones existentes; su aprobacion juridica NO se infiere."""
    _permiso_documental(actor, 'add_planpaquetedocumental')
    key = _clave_documental(clave_idempotencia)
    ahora = ahora or timezone.now()
    if (not isinstance(ahora, datetime) or not timezone.is_aware(ahora)
            or type(codigo) is not str or not re.fullmatch(r'[a-z0-9_-]{1,64}', codigo)
            or type(version) is not str or not re.fullmatch(r'[A-Za-z0-9._-]{1,16}', version)):
        raise ValidationError('IDENTIFICACION_PAQUETE_INVALIDA')
    with transaction.atomic():
        case = _expediente_documental(solicitud_id, actor, bloquear=True)
        context = _contexto_documental(case, contexto)
        rows = _documentos(configuracion, ahora)
        body = {'schema': 'plan-paquete-documental-v1', 'solicitudId': str(case.pk),
            'codigo': codigo, 'version': version, 'etapa': rows[0]['etapa'],
            'perfilDeclarado': configuracion['perfil'], 'proveedor': 'AUTENTIC',
            'documentos': rows, 'contexto': context, 'actorId': actor.pk,
            'claveIdempotencia': str(key), 'estado': 'PROPUESTA_NO_APTA_ENVIO',
            'homologacionFirmaAcreditada': False, 'envioHabilitado': False,
            'aceptacionTitularAutenticada': False, 'firmaAcreditada': False,
            'contenidoPDFSellado': False, 'estadoEnvioPaquete': 'NO_INICIADO_POR_ESTE_PLAN',
            'estadoFirma': 'NO_ACREDITADA', 'rechazoProveedor': None, 'caducidadProveedor': None,
            'referenciaProveedor': None, 'evidenciaProveedorSha256': None}
        digest = _hash_evidencia(body)
        existing = PlanPaqueteDocumental.objects.filter(solicitud=case, clave_idempotencia=key).first()
        if existing:
            if existing.sha256 != digest or existing.evidencia != body:
                raise ValidationError('IDEMPOTENCIA_PAQUETE_CONFLICTO')
            return existing
        if PlanPaqueteDocumental.objects.filter(solicitud=case, codigo=codigo, version=version).exists():
            raise ValidationError('VERSION_PAQUETE_YA_SELLADA')
        result = PlanPaqueteDocumental.objects.create(solicitud=case, codigo=codigo, version=version,
            etapa=rows[0]['etapa'], clave_idempotencia=key, actor=actor, evidencia=body, sha256=digest)
        _auditar_documento(case, actor, 'documento_paquete_propuesto', result)
        return result


def leer_paquetes(solicitud_id, *, actor, ahora=None):
    _permiso_documental(actor, 'view_planpaquetedocumental')
    case = _expediente_documental(solicitud_id, actor)
    ahora = ahora or timezone.now()
    rows = []
    for plan in PlanPaqueteDocumental.objects.filter(solicitud=case):
        body = plan.evidencia
        if (type(body) is not dict or plan.sha256 != _hash_evidencia(body)
                or body.get('solicitudId') != str(case.pk) or body.get('actorId') != plan.actor_id
                or body.get('codigo') != plan.codigo or body.get('version') != plan.version
                or body.get('etapa') != plan.etapa or body.get('proveedor') != 'AUTENTIC'
                or body.get('estado') != 'PROPUESTA_NO_APTA_ENVIO'
                or body.get('envioHabilitado') is not False or body.get('firmaAcreditada') is not False):
            raise ValidationError('PAQUETE_NO_INTEGRO')
        # Historical proposal survives edits/expiry, but must not be presented as current.
        current = body['contexto'] == _contexto_documental(case, body['contexto']['declarado'])
        for item in body['documentos']:
            version = VersionDocumental.objects.select_related('documento').filter(pk=item['versionId']).first()
            if not version or version.sha256 != item['versionSha256']:
                raise ValidationError('PAQUETE_NO_INTEGRO')
            try:
                _verificar_version(version, version.documento, ahora)
            except ValidationError:
                current = False
        rows.append({'id': str(plan.pk), 'codigo': plan.codigo, 'version': plan.version,
            'etapa': plan.etapa, 'proveedor': 'AUTENTIC', 'sha256': plan.sha256,
            'estado': body['estado'], 'versionesVigentesYContextoActual': current,
            'documentos': body['documentos'], 'envioHabilitado': False, 'firmaAcreditada': False,
            'contenidoPDFSellado': False, 'estadoEnvioPaquete': 'NO_INICIADO_POR_ESTE_PLAN',
            'estadoFirma': 'NO_ACREDITADA', 'referenciaProveedor': None,
            'evidenciaProveedorSha256': None, 'rechazoProveedor': None, 'caducidadProveedor': None})
    return rows
