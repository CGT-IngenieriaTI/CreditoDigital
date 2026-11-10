"""Firma especifica del piloto con ConsentimientoConsumo y OTP existentes."""

import base64
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

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


def obtener_autogestion(solicitud_id, identity, *, lock=False):
    """Same submitted dossier, authorized by its holder, never a staff surrogate."""
    from django.db import connections
    from django.http import Http404
    from . import autoservicio
    if (getattr(settings,'CREDIHOY_AUTORIZACION_TITULAR_ENABLED',False) is not True
            or identity.get('_scopeAutorizacion')!='solicitudes:autorizar'):
        raise OperacionError('AUTORIZACION_TITULAR_NO_HABILITADA')
    listed=str(solicitud_id) in getattr(settings,'CREDIHOY_AUTORIZACION_SOLICITUDES',())
    if not listed:
        from .publico_controles import admision_ensayo_vigente
        if not admision_ensayo_vigente():raise Http404
    # The signed authorization scope includes reading this holder's own document.
    autoservicio.habilitar_lectura({**identity,'_scopeLectura':'solicitudes:leer'})
    alias=getattr(settings,'CREDIHOY_AUTORIZACION_DB_ALIAS','')
    db=settings.DATABASES.get(alias,{})
    if (alias not in {'cotizacion','default'} or (getattr(settings,'CREDIHOY_PUBLICO_RESTRINGIDO',False)
            and (alias!='cotizacion' or db.get('NAME')!='credito_digital'
                or db.get('USER')!='credihoy_titular_cotizacion'))):
        raise OperacionError('ALIAS_AUTORIZACION_NO_ACREDITADO')
    if lock and not connections[alias].in_atomic_block:raise ValueError('AUTORIZACION_REQUIERE_TRANSACCION')
    query=Solicitud.objects.using(alias).select_related('solicitante')
    try:
        case=query.get(pk=UUID(str(solicitud_id)),producto=PRODUCTO,
            solicitante__tipo_identificacion='CC',solicitante__numero_identificacion=identity['numeroDocumento'],
            metadatos__modo=autoservicio.MODO,metadatos__origen=autoservicio.ORIGEN)
    except Solicitud.DoesNotExist:raise Http404 from None
    if any(case.metadatos.get(k)!=v for k,v in (('productoLinix','390'),('modalidadLinix','111'),('destinacionLinix','CFA'))):
        raise OperacionError('PERFIL_AUTOGESTION_INCOMPATIBLE')
    details=SolicitudConsumo.objects.using(alias)
    # The writer can lock the consumption row, not Solicitud or its holder.
    if lock:details=details.select_for_update()
    detail=details.get(solicitud=case)
    binding=detail.orchestration_data.get(autoservicio.VINCULO_CONSUMO,{})
    if (binding.get('solicitudId')!=str(case.pk)
            or binding.get('operacionEnvioOriginal')!=case.metadatos.get('envio',{}).get('operacionId')):
        raise OperacionError('VINCULO_ENVIO_AUTENTICO_REQUERIDO')
    case.consumo_detail=detail
    autoservicio.validar_vinculo_consumo(case)
    return case,alias


def presentar_autogestion(solicitud_id,identity,*,campos,clave):
    from django.core import signing
    from apps.documentos.formularios_credihoy import custodiar_formato,recuperar_formato
    import base64
    case,_=obtener_autogestion(solicitud_id,identity)
    existing=documento_personal_existente(case,identity)
    if existing:
        return {**presentar_documento_existente(case,identity,existing),'solicitudId':str(case.pk),
            'estado':'PENDIENTE_ACEPTACION_PERSONAL','firmado':False,'consultasHabilitadas':False}
    if type(campos) is not dict or set(campos)!={'nombre','ciudad'}:
        raise ValueError('DATOS_PERSONALES_COLO023_REQUERIDOS')
    today=timezone.localdate()
    values={**campos,'identificacion':case.solicitante.numero_identificacion,
        'celular':case.solicitante.celular,'fechaDia':str(today.day),'fechaMes':str(today.month),'fechaAnio':str(today.year)}
    fields={k:{'valor':v,'referencia':f'TITULAR:{identity["_sesionReferencia"]}:{case.pk}',
        'verificado':True} for k,v in values.items()}
    meta=custodiar_formato(case.pk,actor=None,titular=identity,codigo='COLO-FO.023',campos=fields,clave_idempotencia=clave)
    pdf,_=recuperar_formato(case.pk,actor=None,titular=identity,operacion_id=clave)
    binding={'solicitudId':str(case.pk),'sesion':identity['_sesionReferencia'],
        'operacionId':meta['operacionId'],'pdfSha256':meta['pdfSha256'],'version':meta['version'],
        'proposito':'AUTORIZACION_CENTRALES_PRECONSULTA'}
    token=signing.dumps(binding,key=settings.CREDIHOY_TITULAR_SECRET,salt='credihoy-colo023-personal-v1')
    return {'solicitudId':str(case.pk),'codigo':'COLO-FO.023','version':meta['version'],
        'pdfSha256':meta['pdfSha256'],'pdfBase64':base64.b64encode(pdf).decode('ascii'),
        'presentacion':token,'operacionId':meta['operacionId'],'proposito':binding['proposito'],
        'estado':'PENDIENTE_ACEPTACION_PERSONAL','firmado':False,'consultasHabilitadas':False}


def aceptar_autogestion(solicitud_id,identity,*,presentacion,acepta,clave):
    from django.core import signing
    from apps.documentos.formularios_credihoy import recuperar_formato
    from pypdf import PdfReader
    from io import BytesIO
    key=str(UUID(str(clave)))
    if acepta is not True:raise ValueError('ACEPTACION_PERSONAL_EXPLICITA_REQUERIDA')
    case,alias=obtener_autogestion(solicitud_id,identity)
    binding=signing.loads(presentacion,key=settings.CREDIHOY_TITULAR_SECRET,
        salt='credihoy-colo023-personal-v1',max_age=600,fallback_keys=[])
    if (binding.get('solicitudId')!=str(case.pk) or binding.get('sesion')!=identity['_sesionReferencia']
            or binding.get('proposito')!='AUTORIZACION_CENTRALES_PRECONSULTA'):
        raise ValueError('PRESENTACION_AUTORIZACION_DE_OTRO_TITULAR')
    pdf,meta=recuperar_formato(case.pk,actor=None,titular=identity,operacion_id=binding['operacionId'])
    if binding['pdfSha256']!=meta['pdfSha256'] or binding['version']!=meta['version']:
        raise ValueError('DOCUMENTO_AUTORIZACION_CAMBIO')
    text='\n'.join(p.extract_text() or '' for p in PdfReader(BytesIO(pdf)).pages)
    if not text.strip():raise ValueError('TEXTO_OFICIAL_NO_RECUPERABLE')
    with transaction.atomic(using=alias):
        case,_=obtener_autogestion(case.pk,identity,lock=True)
        previous=ConsentimientoConsumo.objects.using(alias).filter(solicitud=case).first()
        if previous:
            if (not previous.aceptado or previous.solicitud_id!=case.pk
                    or previous.version!='COLO-FO.023:v'+meta['version']
                    or previous.text_snapshot!=text
                    or previous.text_hash!=build_consent_hash(previous.version,text)
                    or previous.evidencia.get('workflow')!='COLO023_AUTOGESTION_V1'
                    or previous.evidencia.get('proposito')!=binding['proposito']
                    or previous.evidencia.get('pdfSha256')!=meta['pdfSha256']
                    or previous.evidencia.get('originalSha256')!=meta['originalSha256']
                    or not previous.evidencia.get('claveAceptacion')
                    or not previous.evidencia.get('sesionReferencia')):
                raise ValueError('AUTORIZACION_EXISTENTE_NO_SOBRESCRIBIR')
            # A fresh presentation can recover an earlier personal act. Its key,
            # original session and acceptance evidence remain immutable.
            original_key=previous.evidencia.get('documentoOperacionId')
            original=case.consumo_detail.orchestration_data.get('credihoy_pdf_oficiales_v1',{}).get(original_key,{})
            if (original.get('metadata')!=meta
                    or original.get('actorId')!='titular:'+previous.evidencia['sesionReferencia']
                    or not original.get('contenidoCifrado')):
                raise ValueError('AUTORIZACION_EXISTENTE_DOCUMENTO_NO_ACREDITADO')
            return {'solicitudId':str(case.pk),'estado':previous.evidencia['estado'],
                'firmado':previous.firmado,'consultasHabilitadas':False}
        detail=SolicitudConsumo.objects.using(alias).select_for_update().get(solicitud=case)
        if detail.estado!='CONSENTIMIENTO_PENDIENTE':raise ValueError('ETAPA_AUTORIZACION_NO_DISPONIBLE')
        document_key=binding['operacionId']
        stored=detail.orchestration_data['credihoy_pdf_oficiales_v1'][document_key]
        if stored['actorId']!='titular:'+identity['_sesionReferencia']:
            from uuid import NAMESPACE_URL,uuid5
            from apps.documentos.formularios_credihoy import _custodiar_pdf
            # Same immutable PDF, a fresh custody presentation, never regeneration.
            document_key=str(uuid5(NAMESPACE_URL,'credihoy-representacion:'+document_key+':'+identity['_sesionReferencia']))
            _custodiar_pdf(case,actor=None,titular=identity,alias=alias,
                actor_ref='titular:'+identity['_sesionReferencia'],key=document_key,pdf=pdf,meta=meta,
                event='CREDIHOY_PDF_OFICIAL_CUSTODIADO')
        ConsentimientoConsumo.objects.using(alias).create(solicitud=case,otp=None,aceptado=True,firmado=False,
            version='COLO-FO.023:v'+meta['version'],canal=identity['canal'],fecha_aceptacion=timezone.now(),text_snapshot=text,
            text_hash=build_consent_hash('COLO-FO.023:v'+meta['version'],text),tipo_firma='AUTENTIC_PENDIENTE',
            evidencia={'workflow':'COLO023_AUTOGESTION_V1','estado':'PENDIENTE_FIRMA_AUTENTIC',
                'proposito':binding['proposito'],'pdfSha256':meta['pdfSha256'],'originalSha256':meta['originalSha256'],
                'documentoOperacionId':document_key,'claveAceptacion':key,
                'sesionReferencia':identity['_sesionReferencia'],'otp9AReutilizado':False})
        AuditLog.objects.using(alias).create(solicitud=case,event='credihoy_colo023_aceptado_personalmente',
            actor='titular:9A',request_id=key,payload={'codigo':'COLO-FO.023','pdfSha256':meta['pdfSha256'],'firmado':False})
    return {'solicitudId':str(case.pk),'estado':'PENDIENTE_FIRMA_AUTENTIC','firmado':False,'consultasHabilitadas':False}


def preflight_autorizacion_autogestion(solicitud_id,identity):
    case,alias=obtener_autogestion(solicitud_id,identity)
    consent=ConsentimientoConsumo.objects.using(alias).filter(solicitud=case).first()
    if consent is None:
        document=documento_personal_existente(case,identity)
        if document:return {'solicitudId':str(case.pk),'estado':'PENDIENTE_ACEPTACION_PERSONAL',
            'documento':presentar_documento_existente(case,identity,document),'firmado':False,'consultasHabilitadas':False}
    form=SolicitudConsumo.objects.using(alias).get(solicitud=case).form_data
    # The current provider client only attests bytes/hash, not the signer's signature.
    return {'solicitudId':str(case.pk),'estado':'AUTORIZACION_AUSENTE' if consent is None else 'FIRMA_AUTENTIC_PENDIENTE_VERIFICACION',
        'evaluacionHabilitada':False,'HDC':False,'PRESELECTA':False,
        'pendiente':'AUTENTIC_ESTADO_FIRMANTE_DOCUMENT_NUMBER_Y_FIRMA_INTEGRA_VERIFICABLE',
        'datosFormularioPendientes':[k for k in ('monto','plazoMeses','periodicidadPago','recogerCredihoy') if k not in form],
        'enlacePersistencia':'funcional.registrar_resultado_consulta_hdc',
        'adaptacionPendiente':'VERIFICACION_AUTENTIC_Y_DATOS_ECONOMICOS_VIGENTES',
        'componentesExistentes':{'consulta':'OrquestadorGateway','hechos':'credihoy_facts_v2',
            'reglas':'evaluar_reglas_unico_v2','capacidad':'evaluar_capacidad_unico_v2'},
        'evaluacionCreada':False,'Oracle':0,'START':0}


def verificar_autorizacion_crediticia_personal(case, consent, *, exigir_registro=True):
    """Read-only signature gate shared by HDC and real evaluation persistence."""
    import base64
    import json
    from apps.integraciones.autentic.evidencia import FirmaAutenticVerificada, verificar_firma_documental
    from apps.integraciones.autentic.contratos import AutenticError
    from apps.documentos.formularios_credihoy import FORMATOS
    from .autoservicio import MODO, ORIGEN, VINCULO_CONSUMO
    try:
        data=case.consumo_detail.orchestration_data
        binding=data[VINCULO_CONSUMO]
        evidence=consent.evidencia
        if (case.metadatos.get('modo')!=MODO or case.metadatos.get('origen')!=ORIGEN
                or case.asesor_id is not None or case.producto!=PRODUCTO
                or binding['solicitudId']!=str(case.pk)
                or binding['operacionEnvioOriginal']!=case.metadatos['envio']['operacionId']
                or consent.solicitud_id!=case.pk or not consent.aceptado or consent.otp_id is not None
                or consent.version!='COLO-FO.023:v10' or not consent.text_snapshot.strip()
                or consent.text_hash!=build_consent_hash(consent.version,consent.text_snapshot)
                or consent.fecha_aceptacion>timezone.now()
                or evidence['workflow']!='COLO023_AUTOGESTION_V1'
                or evidence['proposito']!='AUTORIZACION_CENTRALES_PRECONSULTA'
                or evidence['otp9AReutilizado'] is not False
                or evidence['originalSha256']!=FORMATOS['COLO-FO.023']['sha256']):
            raise ValueError
        if exigir_registro and (consent.firmado is not True or consent.tipo_firma!='AUTENTIC_VERIFICADA'
                or evidence.get('estado')!='AUTORIZACION_FIRMADA_VERIFICADA'):
            raise ValueError
        doc_id=evidence['documentoOperacionId']
        stored=data['credihoy_pdf_oficiales_v1'][doc_id]
        original_envelope=json.loads(decrypt_text(stored['contenidoCifrado']))
        original=base64.b64decode(original_envelope['pdfBase64'],validate=True)
        if (original_envelope['solicitudId']!=str(case.pk) or original_envelope['operacionId']!=doc_id
                or original_envelope['metadata']!=stored['metadata']
                or stored['metadata']['pdfSha256']!=evidence['pdfSha256']
                or hashlib.sha256(original).hexdigest()!=evidence['pdfSha256']):raise ValueError
        history=data.get('credihoy_autentic_envios_v1',{})
        matches=[(k,v) for k,v in history.items() if v.get('vinculo',{}).get('documentoOperacionId')==doc_id]
        if len(matches)!=1:raise ValueError
        send_id,entry=matches[0];link=entry['vinculo']
        person_hash=hashlib.sha256(case.solicitante.numero_identificacion.encode()).hexdigest()
        if (entry['estado']!='FIRMA_REPORTADA_NO_VERIFICADA'
                or link['solicitudId']!=str(case.pk) or link['pdfSha256']!=evidence['pdfSha256']
                or link['firmanteReferencia']!=person_hash or link['codigo']!='COLO-FO.023'):raise ValueError
        receipt=json.loads(decrypt_text(entry['reciboCifrado']))
        downloaded=json.loads(decrypt_text(entry['descargaCifrada']))
        signed=base64.b64decode(downloaded['pdfBase64'],validate=True)
        if (downloaded['solicitudId']!=str(case.pk) or downloaded['envioId']!=send_id
                or downloaded['originalSha256']!=evidence['pdfSha256']
                or hashlib.sha256(signed).hexdigest()!=downloaded['pdfSha256']
                or downloaded['transaccionReferencia']!=hashlib.sha256(receipt['transaccion'].encode()).hexdigest()):
            raise ValueError
        # documentNumber belongs to certification. Its absence must not replace
        # the independent mandatory identity/participation signature verification.
        number=entry.get('documentNumber')
        if number is not None and (type(number) is not str or not number.strip()):raise ValueError
        verified=verificar_firma_documental(original=original,firmado=signed,
            transaccion=receipt['transaccion'],document_number=number,
            documento_identidad=case.solicitante.numero_identificacion,
            contexto={'solicitudId':str(case.pk),'envioId':send_id,'documentoOperacionId':doc_id,
            'vinculo':link,'recibo':receipt,'descarga':downloaded,'estado':entry['estado'],
                'preparadoEn':entry.get('preparadoEn')})
        if (type(verified) is not FirmaAutenticVerificada or not verified.referencia_verificacion
                or verified.transaccion!=receipt['transaccion'] or verified.document_number!=number
                or verified.firmante_sha256!=person_hash or verified.original_sha256!=evidence['pdfSha256']
                or verified.firmado_sha256!=downloaded['pdfSha256']
                or verified.firmado_en.utcoffset() is None or verified.vigente_hasta.utcoffset() is None
                or not consent.fecha_aceptacion<=verified.firmado_en<=timezone.now()<verified.vigente_hasta):raise ValueError
        if exigir_registro and evidence.get('firmaReferencia')!=verified.referencia_verificacion:raise ValueError
        return verified
    except (KeyError,TypeError,ValueError,AttributeError,AutenticError):
        raise OperacionError('AUTORIZACION_PERSONAL_FIRMADA_Y_VIGENTE_REQUERIDA') from None


def registrar_firma_autogestion(solicitud_id):
    """Trusted backend only; the public writer has no UPDATE on consent."""
    if getattr(settings,'CREDIHOY_AUTORIZACION_REGISTRO_FIRMA_ENABLED',False) is not True:
        raise OperacionError('REGISTRO_FIRMA_PERSONAL_CERRADO')
    with transaction.atomic():
        case=Solicitud.objects.select_for_update().select_related('solicitante').get(pk=solicitud_id)
        SolicitudConsumo.objects.select_for_update().get(solicitud=case)
        consent=ConsentimientoConsumo.objects.select_for_update().get(solicitud=case)
        verified=verificar_autorizacion_crediticia_personal(case,consent,exigir_registro=False)
        if consent.firmado:
            verificar_autorizacion_crediticia_personal(case,consent)
            return consent
        consent.firmado=True;consent.tipo_firma='AUTENTIC_VERIFICADA'
        consent.evidencia={**consent.evidencia,'estado':'AUTORIZACION_FIRMADA_VERIFICADA',
            'firmaReferencia':verified.referencia_verificacion,'fechaFirma':verified.firmado_en.isoformat(),
            'vigenteHasta':verified.vigente_hasta.isoformat()}
        consent.save(update_fields=['firmado','tipo_firma','evidencia','updated_at'])
        AuditLog.objects.create(solicitud=case,event='CREDIHOY_COLO023_FIRMA_VERIFICADA',
            actor='sistema:verificacion-documental',payload={'pdfSha256':verified.original_sha256,
                'firmadoSha256':verified.firmado_sha256,'referencia':verified.referencia_verificacion})
        return consent


def operar_autogestion(identity,payload):
    if type(payload) is not dict or payload.get('operacion')!='autorizacion':raise ValueError('CONTRATO_AUTORIZACION_INVALIDO')
    action=payload.get('accion');base={'operacion','accion','solicitudId'}
    expected={'presentar':base|{'campos','clave'},'aceptar':base|{'presentacion','acepta','clave'},'consultar':base,
        'ver_firma':base|{'clave'},'verificar_firmante':base|{'clave','presentacion','autorizaFirma'},
        'consultar_complemento':base,'guardar_complemento':base|{'operacionId','version','datos','confirma'},
        'centrales_presentar':base|{'operacionId'},'centrales_enviar':base|{'operacionId','presentacion','acepta'},
        'centrales_consultar':base,'centrales_resultado':base|{'comprobacion'}}
    if action not in expected or set(payload)!=expected[action]:raise ValueError('CONTRATO_AUTORIZACION_INVALIDO')
    reference=str(UUID(payload['solicitudId']))
    if reference!=payload['solicitudId'] or identity.get('canal') not in {'SMS','EMAIL'}:raise ValueError('IDENTIDAD_AUTORIZACION_INVALIDA')
    if action.startswith('centrales_'):
        from . import centrales_personal as central
        if action=='centrales_presentar':result=central.presentar(reference,identity,payload['operacionId'])
        elif action=='centrales_enviar':result=central.aceptar(reference,identity,payload['operacionId'],payload['presentacion'],payload['acepta'])
        elif action=='centrales_resultado':
            central.registrar_resultado(reference,identity,payload['comprobacion'])
            result=central.consultar(reference,identity)
        else:result=central.consultar(reference,identity)
        document=result.get('documento')
        if document is not None:document={k:document[k] for k in ('codigo','version','pdfSha256','pdfBase64','presentacion','operacionId','proposito')}
        return {'versionContrato':'credihoy-autorizacion-titular-v1','solicitudId':reference,
            'estado':'CENTRALES_'+result['centrales']['estado'],'documento':document,
            'firmado':False,'consultasHabilitadas':False,'evaluacionHabilitada':False,'centrales':result['centrales']}
    if action in {'consultar_complemento','guardar_complemento'}:
        from .autoservicio import operar_complemento_personal
        complement=operar_complemento_personal(reference,identity,payload)
        return {'versionContrato':'credihoy-autorizacion-titular-v1','solicitudId':reference,
            'estado':'COMPLEMENTO_REGISTRADO' if complement['version'] else 'COMPLEMENTO_PENDIENTE',
            'documento':None,'firmado':False,'consultasHabilitadas':False,'evaluacionHabilitada':False,
            'complemento':complement}
    signature=None
    if action=='ver_firma':
        result=ver_documento_para_firma(reference,identity,clave=payload['clave'])
        document=result['documento'];signature=result['firma']
    elif action=='verificar_firmante':
        result=acreditar_firma_sin_envio(reference,identity,clave=payload['clave'],presentacion=payload['presentacion'],
            autoriza_firma=payload['autorizaFirma']);document=None;signature=result['firma']
    elif action=='presentar':
        result=presentar_autogestion(reference,identity,campos=payload['campos'],clave=payload['clave'])
        document={k:result[k] for k in ('codigo','version','pdfSha256','pdfBase64','presentacion','operacionId','proposito')}
    elif action=='aceptar':
        result=aceptar_autogestion(reference,identity,presentacion=payload['presentacion'],acepta=payload['acepta'],clave=payload['clave'])
        document=None
    else:
        result=preflight_autorizacion_autogestion(reference,identity);document=result.get('documento')
    return {'versionContrato':'credihoy-autorizacion-titular-v1','solicitudId':reference,
        'estado':result['estado'],'documento':document,'firmado':False,'consultasHabilitadas':False,
        'evaluacionHabilitada':False,**({'firma':signature} if signature is not None else {})}


def ver_documento_para_firma(solicitud_id,identity,*,clave):
    """Read the already accepted document; never recapture or send it."""
    import base64
    from django.core import signing
    from apps.documentos.formularios_credihoy import recuperar_formato
    from .publico_seguimiento import key as secret,SALT_INTENCION_FIRMA
    case,alias=obtener_autogestion(solicitud_id,identity);key=str(UUID(str(clave)))
    c=ConsentimientoConsumo.objects.using(alias).get(solicitud=case)
    if (not c.aceptado or c.firmado or c.version!='COLO-FO.023:v10'
            or c.evidencia.get('workflow')!='COLO023_AUTOGESTION_V1'):
        raise ValueError('ACEPTACION_PREVIA_OFICIAL_REQUERIDA')
    history=SolicitudConsumo.objects.using(alias).get(solicitud=case).orchestration_data.get('credihoy_autentic_envios_v1',{})
    if history:raise ValueError('ENVIO_EXISTENTE_CONSULTAR_ESTADO_SIN_REPETIR')
    pdf,meta=recuperar_formato(case.pk,actor=None,titular=identity,operacion_id=c.evidencia['documentoOperacionId'])
    if meta['pdfSha256']!=c.evidencia['pdfSha256']:raise ValueError('DOCUMENTO_ACEPTADO_CAMBIO')
    p={'solicitudId':str(case.pk),'sesion':identity['_sesionReferencia'],'operacionFirmaId':key,
        'documentoOperacionId':c.evidencia['documentoOperacionId'],'pdfSha256':meta['pdfSha256'],
        'aceptacionId':c.evidencia['claveAceptacion'],'proposito':'AUTORIZACION_CENTRALES_PRECONSULTA'}
    return {'estado':'PENDIENTE_FIRMA_AUTENTIC','documento':{'codigo':'COLO-FO.023','version':'10',
        'pdfSha256':meta['pdfSha256'],'pdfBase64':base64.b64encode(pdf).decode(),
        'presentacion':signing.dumps(p,key=secret(),salt=SALT_INTENCION_FIRMA),
        'operacionId':p['documentoOperacionId'],'proposito':p['proposito']},
        'firma':{'operacionId':key,'envioHabilitado':False,'identidadVerificada':False}}


def acreditar_firma_sin_envio(solicitud_id,identity,*,clave,presentacion,autoriza_firma):
    """Canonical identity + exact accepted PDF, no Autentic client invocation."""
    from .publico_seguimiento import verificar_presentacion_firma
    key=str(UUID(str(clave)));case,alias=obtener_autogestion(solicitud_id,identity)
    p=verificar_presentacion_firma(presentacion,solicitud_id=case.pk,sesion=identity['_sesionReferencia'],clave=key)
    c=ConsentimientoConsumo.objects.using(alias).get(solicitud=case)
    if any(p[k]!=c.evidencia[e] for k,e in (('pdfSha256','pdfSha256'),
            ('documentoOperacionId','documentoOperacionId'),('aceptacionId','claveAceptacion'))):
        raise ValueError('ACEPTACION_PERSONAL_CAMBIO')
    _,proof=preparar_envio_autogestion(case.pk,identity,identidad_firmante=identity.get('_firmanteInstitucional'),
        clave=key,autoriza_firma=autoriza_firma)
    with transaction.atomic(using=alias):
        obtener_autogestion(case.pk,identity,lock=True)
        # Waiting for a competing row lock must not extend a short-lived proof.
        _,current=preparar_envio_autogestion(case.pk,identity,identidad_firmante=identity.get('_firmanteInstitucional'),
            clave=key,autoriza_firma=autoriza_firma)
        if current!=proof:raise ValueError('IDENTIDAD_FIRMA_CAMBIO')
        detail=SolicitudConsumo.objects.using(alias).select_for_update().get(solicitud=case)
        previous=detail.orchestration_data
        if previous.get('credihoy_autentic_envios_v1'):raise ValueError('ENVIO_EXISTENTE_CONCILIAR')
        checks=dict(previous.get('credihoy_autentic_identidad_v1',{}))
        if key in checks:
            if any(checks[key][k]!=proof[k] for k in ('solicitudId','titularReferencia','pdfSha256',
                    'documentoOperacionId','aceptacionId','sesionReferencia','operacionFirmaId')):
                raise ValueError('INTENCION_IDENTIDAD_CONFLICTIVA')
        else:
            if len(checks)>=5:raise ValueError('VERIFICACIONES_IDENTIDAD_AGOTADAS')
            checks[key]=proof
            detail.orchestration_data={**previous,'credihoy_autentic_identidad_v1':checks}
            detail.save(using=alias,update_fields=['orchestration_data','updated_at'])
            # Minimal existing audit, no identity names, credentials, PDF or secureKey.
            AuditLog.objects.using(alias).create(solicitud=case,event='credihoy_colo023_identidad_firma_acreditada',
                actor='titular:9A',request_id=key,payload=proof)
    return {'estado':'FIRMA_AUTENTIC_PENDIENTE_VERIFICACION',
        'firma':{'operacionId':key,'envioHabilitado':False,'identidadVerificada':True}}


def preparar_envio_autogestion(solicitud_id,identity,*,identidad_firmante,clave,autoriza_firma):
    """Read-only preparation, no network or consent/signature state writes.

    SOP-MN-3 V4 p.7 explicitly permits a consultable/verifiable own secureKey
    nomenclature (manual SHA bfccedbc7ec1ad75c6524308e871f2e91091b2075e059f41e42618f466a30aee).
    This evidence is NOT a provider OTP, not a signed PDF, and not permission to
    call the provider. The separate personal sending gate remains off by default.
    """
    from apps.documentos.formularios_credihoy import recuperar_formato
    from apps.integraciones.autentic.contratos import preparar_firma
    from pypdf import PdfReader
    from io import BytesIO
    if autoriza_firma is not True:raise ValueError('ACTO_PERSONAL_DE_FIRMA_REQUERIDO')
    key=str(UUID(str(clave)))
    case,alias=obtener_autogestion(solicitud_id,identity)
    consent=ConsentimientoConsumo.objects.using(alias).get(solicitud=case)
    if (consent.evidencia.get('workflow')!='COLO023_AUTOGESTION_V1' or not consent.aceptado
            or consent.firmado or consent.evidencia.get('estado')!='PENDIENTE_FIRMA_AUTENTIC'
            or consent.otp_id is not None or consent.version!='COLO-FO.023:v10'
            or consent.evidencia.get('proposito')!='AUTORIZACION_CENTRALES_PRECONSULTA'
            or consent.evidencia.get('otp9AReutilizado') is not False
            or consent.fecha_aceptacion>timezone.now()
            or consent.text_hash!=build_consent_hash(consent.version,consent.text_snapshot)):
        raise ValueError('ACEPTACION_PERSONAL_DOCUMENTO_REQUERIDA')
    history=SolicitudConsumo.objects.using(alias).get(solicitud=case).orchestration_data.get('credihoy_autentic_envios_v1',{})
    if any(k!=key and e.get('vinculo',{}).get('pdfSha256')==consent.evidencia['pdfSha256'] for k,e in history.items()):
        raise ValueError('ENVIO_EXISTENTE_CONCILIAR_SIN_REENVIAR')
    pdf,meta=recuperar_formato(case.pk,actor=None,titular=identity,operacion_id=consent.evidencia['documentoOperacionId'])
    if meta['pdfSha256']!=consent.evidencia['pdfSha256']:raise ValueError('DOCUMENTO_ACEPTADO_CAMBIO')
    from .publico_seguimiento import verificar_firmante
    signer=verificar_firmante(identidad_firmante,identity,solicitud_id=case.pk,
        pdf_sha256=meta['pdfSha256'],clave=key)
    metadata={k:signer[k] for k in ('names','lastNames')}
    fields=PdfReader(BytesIO(pdf)).get_fields() or {}
    pdf_name=str(fields.get('Nombre',{}).get('/V',''))
    normalized=lambda v:' '.join(v.split()).casefold()
    surname=normalized(case.solicitante.primer_apellido)
    if (normalized(pdf_name)!=normalized(metadata['names']+' '+metadata['lastNames'])
            or not surname or not (normalized(metadata['lastNames'])==surname
                or normalized(metadata['lastNames']).startswith(surname+' '))):
        raise ValueError('NOMBRES_NO_CORRESPONDEN_AL_PDF_ACEPTADO')
    evidence={'version':'COLO023_FIRMA_PERSONAL_V1','solicitudId':str(case.pk),
        'titularReferencia':hashlib.sha256(case.solicitante.numero_identificacion.encode()).hexdigest(),
        'documentoOperacionId':consent.evidencia['documentoOperacionId'],'pdfSha256':meta['pdfSha256'],
        'consentimientoId':consent.pk,'aceptacionId':consent.evidencia['claveAceptacion'],
        'aceptadoEn':consent.fecha_aceptacion.isoformat(),'sesionReferencia':identity['_sesionReferencia'],
        'sesionExpiraAt':identity['_sesionExpiraAt'],'operacionFirmaId':key,
        'actoPersonalFirma':True,'OTP9AEsFirma':False,'proposito':'AUTORIZACION_CENTRALES_PRECONSULTA',
        'nombresFuente':signer['fuente'],'identidadFuente':'LINIX_9A_VERIFY_Y_DELEGACION_VIGENTES',
        'identidadRequestId':signer['requestId'],'identidadConsultadaEn':signer['consultadoEn'],
        'identidadVerificadaEn':signer['verificadoEn'],'verificacionReferencia':signer['verificacionReferencia']}
    secure='CREDIHOY-COLO023:'+huella('firma-personal',evidence)
    request=preparar_firma({**{k:v.strip() for k,v in metadata.items()},
        'docId':case.solicitante.numero_identificacion,'secureKey':secure},{'COLO-FO.023.pdf':pdf})
    return request,evidence


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



def documento_personal_existente(case,identity):
    from apps.documentos.formularios_credihoy import recuperar_formato,FORMATOS
    history=case.consumo_detail.orchestration_data.get('credihoy_pdf_oficiales_v1',{})
    matches=[(key,row['metadata']) for key,row in history.items()
        if row.get('metadata',{}).get('codigo')=='COLO-FO.023']
    if not matches:return None
    if len({meta['pdfSha256'] for _,meta in matches})!=1:
        raise ValueError('DOCUMENTOS_PERSONALES_DISTINTOS_CONCILIAR')
    key,meta=matches[-1]
    if (meta.get('version')!='10' or meta.get('originalSha256')!=FORMATOS['COLO-FO.023']['sha256']
            or meta.get('identidadDocumentalReferencia')!=hashlib.sha256(identity['numeroDocumento'].encode()).hexdigest()):
        raise ValueError('DOCUMENTO_PERSONAL_NO_CORRESPONDE_AL_TITULAR')
    pdf,checked=recuperar_formato(case.pk,actor=None,titular=identity,operacion_id=key)
    if checked!=meta:raise ValueError('DOCUMENTO_PERSONAL_CAMBIO')
    return pdf,meta,key


def presentar_documento_existente(case,identity,document):
    from django.core import signing
    import base64
    pdf,meta,key=document
    binding={'solicitudId':str(case.pk),'sesion':identity['_sesionReferencia'],
        'operacionId':key,'pdfSha256':meta['pdfSha256'],'version':meta['version'],
        'proposito':'AUTORIZACION_CENTRALES_PRECONSULTA'}
    token=signing.dumps(binding,key=settings.CREDIHOY_TITULAR_SECRET,salt='credihoy-colo023-personal-v1')
    return {'codigo':'COLO-FO.023','version':meta['version'],'pdfSha256':meta['pdfSha256'],
        'pdfBase64':base64.b64encode(pdf).decode('ascii'),'presentacion':token,
        'operacionId':key,'proposito':binding['proposito']}


@dataclass(frozen=True, repr=False)
class AceptacionContractualOTPVerificada:
    solicitud_id: str
    documento_id: str
    envio_id: str
    pdf_sha256: str
    titular_sha256: str
    secure_key_sha256: str
    aceptado_en: datetime
    verificado_en: datetime
    referencia: str
    canal: str


def preparar_envio_contractual(solicitud_id, identity, clave):
    """Prepare only from the stored accepted/validated act; never creates it."""
    from apps.integraciones.autentic.contratos import preparar_firma
    case, alias = _contexto_contractual(solicitud_id, identity)
    key = str(UUID(str(clave)))
    record = _leer_contractual(case, key)
    binding = record['vinculo']
    pdf, meta = _documento_contractual(case, binding['documentoOperacionId'])
    verified = validar_aceptacion_contractual(case, key, meta['pdfSha256'])
    proof = record['prueba']
    challenge = _challenge_contractual(case, alias, record)
    now = timezone.now()
    if (binding['sesionReferencia'] != identity['_sesionReferencia']
            or binding['sesionExpiraAt'] != identity['_sesionExpiraAt']
            or now >= min(_fecha_contractual(binding['sesionExpiraAt']), challenge.expira_at)
            or verified.referencia != proof['referencia']):
        raise ValueError('OTP_CONTRACTUAL_VENTANA_PREPARACION_CERRADA')
    signer = record['firmante']
    secure = _secure_key_contractual(_base_prueba_contractual(proof))
    metadata = {'names': signer['names'], 'lastNames': signer['lastNames'],
        'docId': case.solicitante.numero_identificacion, 'secureKey': secure,
        'identityValidation': 'NONE', 'identityValidationId': challenge.transaction_uuid,
        'identityValidationPhone': decrypt_text(challenge.destination_full_encrypted)}
    if case.solicitante.email:
        from django.core.validators import validate_email
        validate_email(case.solicitante.email)
        metadata['identityValidationEmail']=case.solicitante.email
    request = preparar_firma(metadata, {meta['codigo']+'.pdf': pdf})
    return request, dict(proof)


def validar_aceptacion_contractual(case, envio_id, pdf_sha256):
    """Strictly read-only historical custody; verify original audits, never add one."""
    alias = case._state.db or 'default'
    key = str(UUID(str(envio_id)))
    with transaction.atomic(using=alias):
        detail = SolicitudConsumo.objects.using(alias).select_related('solicitud__solicitante').get(solicitud_id=case.pk)
        current_case = detail.solicitud
        if current_case.producto != PRODUCTO or current_case.solicitante_id != case.solicitante_id:
            raise ValueError('ACEPTACION_CONTRACTUAL_DE_OTRO_TITULAR')
        proof = _validar_prueba_contractual(current_case, key, pdf_sha256, alias, detail=detail)
    return AceptacionContractualOTPVerificada(str(case.pk), proof['documentoOperacionId'], key,
        proof['pdfSha256'], proof['titularReferencia'], proof['secureKeySha256'],
        _fecha_contractual(proof['aceptadoEn']), _fecha_contractual(proof['verificadoEn']),
        proof['referencia'], proof['canal'])


def _fecha_contractual(value):
    date = datetime.fromisoformat(value)
    if not timezone.is_aware(date):
        raise ValueError('FECHA_CONTRACTUAL_SIN_ZONA')
    return date


def _contexto_contractual(solicitud_id, identity, *, lock=False):
    """Live, separately scoped owner access; no calls back into documentary forms."""
    from django.db import connections
    from .autoservicio import MODO, ORIGEN, VINCULO_CONSUMO
    if getattr(settings, 'CREDIHOY_OTP_CONTRACTUAL_ENABLED', False) is not True:
        raise ValueError('OTP_CONTRACTUAL_NO_HABILITADO')
    try:
        if (type(identity) is not dict or identity.get('_scopeContractual') != CONTRACTUAL_SCOPE
                or str(UUID(identity['_sesionReferencia'])) != identity['_sesionReferencia']
                or _fecha_contractual(identity['_sesionExpiraAt']) <= timezone.now()
                or not re.fullmatch(r'[0-9]{6,15}', identity['numeroDocumento'])):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('SESION_CONTRACTUAL_NO_ACREDITADA') from None
    if getattr(settings,'CREDIHOY_CONTRACTUAL_AMBIENTE','')=='STAGING':
        from .publico_institucional import alcance_contractual
        alcance_contractual(solicitud_id,identity)
    alias = getattr(settings, 'CREDIHOY_OTP_CONTRACTUAL_DB_ALIAS',
                    getattr(settings, 'CREDIHOY_AUTORIZACION_DB_ALIAS', 'default'))
    if alias not in {'default', 'cotizacion'} or alias not in settings.DATABASES:
        raise ValueError('ALIAS_CONTRACTUAL_NO_ADMITIDO')
    if lock and not connections[alias].in_atomic_block:
        raise ValueError('CONTRACTUAL_REQUIERE_TRANSACCION')
    case = Solicitud.objects.using(alias).select_related('solicitante').get(pk=UUID(str(solicitud_id)),
        producto=PRODUCTO, asesor__isnull=True, solicitante__tipo_identificacion='CC',
        solicitante__numero_identificacion=identity['numeroDocumento'], metadatos__modo=MODO, metadatos__origen=ORIGEN)
    query = SolicitudConsumo.objects.using(alias)
    if lock:
        query = query.select_for_update()
    detail = query.get(solicitud_id=case.pk)
    binding = detail.orchestration_data.get(VINCULO_CONSUMO, {})
    if (any(case.metadatos.get(k) != v for k, v in (
            ('productoLinix', '390'), ('modalidadLinix', '111'), ('destinacionLinix', 'CFA')))
            or binding.get('solicitudId') != str(case.pk)
            or not binding.get('operacionEnvioOriginal')
            or binding['operacionEnvioOriginal'] != case.metadatos.get('envio', {}).get('operacionId')):
        raise ValueError('VINCULO_CONTRACTUAL_DE_OTRO_EXPEDIENTE')
    case.consumo_detail = detail
    return case, alias


def _json_contractual(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _digest_contractual(data):
    return hashlib.sha256(_json_contractual(data).encode()).hexdigest()


def _mac_contractual(purpose, data):
    secret = getattr(settings, 'CREDIHOY_OTP_CONTRACTUAL_HMAC_KEY', '')
    if type(secret) is not str or len(secret) < 48:
        raise ValueError('CLAVE_HMAC_CONTRACTUAL_NO_CONFIGURADA')
    return hmac.new(secret.encode(), (purpose+'\x00'+_json_contractual(data)).encode(), 'sha256').hexdigest()


def _secure_key_contractual(base):
    return 'CREDIHOY-CONTRACTUAL:'+_mac_contractual('secure-key', base)


def _base_prueba_contractual(proof):
    return {k: v for k, v in proof.items() if k not in {'referencia', 'secureKeySha256'}}


def _leer_contractual(case, key, *, detail=None):
    if detail is None:
        detail = case.consumo_detail
    try:
        stored = detail.orchestration_data[CONTRACTUAL_HISTORY][key]
        record = json.loads(decrypt_text(stored['contenidoCifrado']))
        if (stored['sha256'] != _digest_contractual(record)
                or record['vinculo']['solicitudId'] != str(case.pk)
                or record['vinculo']['operacionFirmaId'] != key):
            raise ValueError
        return record
    except Exception:
        raise ValueError('CUSTODIA_OTP_CONTRACTUAL_NO_INTEGRA') from None


def _guardar_contractual(case, alias, key, record):
    detail = case.consumo_detail
    history = dict(detail.orchestration_data.get(CONTRACTUAL_HISTORY, {}))
    if key not in history and len(history) >= 20:
        raise ValueError('LIMITE_ACTOS_CONTRACTUALES')
    history[key] = {'sha256': _digest_contractual(record), 'contenidoCifrado': encrypt_text(_json_contractual(record))}
    detail.orchestration_data = {**detail.orchestration_data, CONTRACTUAL_HISTORY: history}
    detail.save(using=alias, update_fields=['orchestration_data', 'updated_at'])


def _auditar_contractual(case, alias, action, key, payload, *, actor='titular:contractual'):
    AuditLog.objects.using(alias).create(solicitud_id=case.pk, event='credihoy_contractual_'+action,
        actor=actor, request_id=key, payload=payload)


def _exigir_auditoria_contractual(case, alias, action, key, payload):
    rows = _auditorias_contractuales(case,alias,'credihoy_contractual_'+action,key)
    if len(rows) != 1 or rows[0] != payload:
        raise ValueError('AUDITORIA_CONTRACTUAL_NO_INTEGRA')


def _documento_contractual(case, key, *, detail=None, historico=False):
    from apps.documentos.formularios_credihoy import FORMATOS
    from apps.integraciones.autentic.contratos import validar_pdf
    try:
        detail = detail if detail is not None else case.consumo_detail
        independent = _auditorias_contractuales(case,detail._state.db,'credihoy_contractual_documento_prueba_custodiado',key)
        if len(independent)>1:raise ValueError
        stored = independent[0] if independent else detail.orchestration_data['credihoy_pdf_oficiales_v1'][key]
        meta = stored['metadata']
        trial=meta['codigo']=='PRUEBA-FIRMA-STAGING'
        if trial:
            if (not independent or meta.get('proposito')!='PRUEBA_INTEGRACION_AUTENTIC'
                    or meta.get('version')!='1' or meta.get('ambienteFirma')!='STAGING'
                    or meta.get('firmaProductivaAcreditada') is not False or meta.get('autorizaDesembolso') is not False
                    or meta.get('originalSha256')!=meta.get('pdfSha256') or not 1<=meta.get('paginas',0)<=10):raise ValueError
            if not historico:
                from .publico_institucional import alcance_contractual
                scope=alcance_contractual(case.pk)
                if (scope.get('documentoPrueba')!={'codigo':meta['codigo'],'version':'1','paginas':meta['paginas']}
                        or scope['documentoOperacionId']!=key or scope['pdfSha256']!=meta['pdfSha256']):raise ValueError
        admitted = getattr(settings, 'CREDIHOY_OTP_CONTRACTUAL_DOCUMENTOS', ('COLO-FO.001',))
        if (meta['codigo'] == 'COLO-FO.023'
                or (not historico and not trial and (meta['codigo'] not in admitted or meta['codigo'] not in FORMATOS
                    or meta['version'] != FORMATOS[meta['codigo']]['version']
                    or meta['originalSha256'] != FORMATOS[meta['codigo']]['sha256']))
                or meta['solicitudId'] != str(case.pk)
                or meta['identidadDocumentalReferencia'] != hashlib.sha256(case.solicitante.numero_identificacion.encode()).hexdigest()):
            raise ValueError
        envelope = json.loads(decrypt_text(stored['contenidoCifrado']))
        pdf = base64.b64decode(envelope['pdfBase64'], validate=True)
        if (envelope['solicitudId'] != str(case.pk) or envelope['operacionId'] != key
                or envelope['metadata'] != meta or validar_pdf(pdf) != meta['pdfSha256']):
            raise ValueError
        audits = independent if trial else _auditorias_contractuales(case,detail._state.db,'CREDIHOY_PDF_OFICIAL_CUSTODIADO',key)
        expected={'codigo':meta['codigo'],'version':meta['version'],'sha256':meta['pdfSha256']}
        if len(audits) != 1 or {k:audits[0].get(k) for k in expected}!=expected:
            raise ValueError
        if not trial and audits[0]!=expected:raise ValueError
        return pdf, dict(meta)
    except Exception:
        raise ValueError('DOCUMENTO_CONTRACTUAL_NO_INTEGRO') from None


@contextmanager
def _transaccion_contractual(solicitud_id, identity):
    from django.db import connections
    case, alias = _contexto_contractual(solicitud_id, identity)
    if connections[alias].vendor != 'postgresql' or connections[alias].in_atomic_block:
        raise ValueError('OTP_CONTRACTUAL_REQUIERE_COMMIT_POSTGRESQL_DURABLE')
    with transaction.atomic(using=alias, durable=True):
        case, _ = _contexto_contractual(solicitud_id, identity, lock=True)
        yield case, alias


def _vinculo_sesion_contractual(record, identity):
    binding = record['vinculo']
    if (binding['proposito'] != CONTRACTUAL_PURPOSE or binding['scope'] != CONTRACTUAL_SCOPE
            or binding['sesionReferencia'] != identity['_sesionReferencia']
            or binding['sesionExpiraAt'] != identity['_sesionExpiraAt']
            or binding['titularReferencia'] != hashlib.sha256(identity['numeroDocumento'].encode()).hexdigest()):
        raise ValueError('ACTO_CONTRACTUAL_DE_OTRA_SESION')


def _firmante_contractual(case, identity, key, pdf, meta):
    """Reuse the existing signed institutional assertion only as a source of names."""
    from .publico_seguimiento import verificar_firmante
    from apps.documentos.formularios_credihoy import MAPA
    from pypdf import PdfReader
    from io import BytesIO
    try:
        signer = verificar_firmante(identity['_firmanteInstitucional'], identity, solicitud_id=case.pk,
            pdf_sha256=meta['pdfSha256'], clave=key, proposito=CONTRACTUAL_PURPOSE)
        fields = PdfReader(BytesIO(pdf)).get_fields() or {}
        name = signer['names']+' '+signer['lastNames'] if meta['codigo']=='PRUEBA-FIRMA-STAGING' else str(fields[MAPA[meta['codigo']]['nombre']]['/V'])
        normalize = lambda value:' '.join(value.split()).casefold()
        surname = normalize(case.solicitante.primer_apellido)
        if (normalize(name) != normalize(signer['names']+' '+signer['lastNames']) or not surname
                or not (normalize(signer['lastNames']) == surname or normalize(signer['lastNames']).startswith(surname+' '))):
            raise ValueError
        return {k: signer[k] for k in ('names', 'lastNames')}
    except Exception:
        raise ValueError('IDENTIDAD_CONTRACTUAL_INSTITUCIONAL_NO_ACREDITADA') from None


def presentar_documento_contractual(solicitud_id, identity, *, documento_operacion_id, clave):
    from django.core import signing
    from .publico_seguimiento import key as signing_key
    key, doc = str(UUID(str(clave))), str(UUID(str(documento_operacion_id)))
    with _transaccion_contractual(solicitud_id, identity) as (case, alias):
        pdf, meta = _documento_contractual(case, doc)
        signer = _firmante_contractual(case, identity, key, pdf, meta)
        binding = {'version': CONTRACTUAL_WORKFLOW, 'proposito': CONTRACTUAL_PURPOSE, 'scope': CONTRACTUAL_SCOPE,
            'solicitudId': str(case.pk), 'titularReferencia': hashlib.sha256(identity['numeroDocumento'].encode()).hexdigest(),
            'sesionReferencia': identity['_sesionReferencia'], 'sesionExpiraAt': identity['_sesionExpiraAt'],
            'documentoOperacionId': doc, 'operacionFirmaId': key, 'pdfSha256': meta['pdfSha256'],
            'codigo': meta['codigo'], 'versionDocumento': meta['version'], 'originalSha256': meta['originalSha256'],
            'firmanteSha256': _digest_contractual(signer)}
        history = case.consumo_detail.orchestration_data.get(CONTRACTUAL_HISTORY, {})
        if key in history:
            if puede_renovar_revision_contractual(case, alias, _leer_contractual(case, key), key):
                return None
            if _leer_contractual(case, key)['vinculo'] != binding:
                raise ValueError('PRESENTACION_CONTRACTUAL_IDEMPOTENCIA_CONFLICTO')
        else:
            antecedents = []
            for other in history:
                previous = _leer_contractual(case, other)
                if previous['vinculo']['pdfSha256'] == binding['pdfSha256']:
                    if not puede_renovar_revision_contractual(case, alias, previous, other):
                        raise ValueError('ACTO_CONTRACTUAL_ACTIVO_O_INCIERTO_NO_DUPLICAR')
                    antecedents.append(other)
            _guardar_contractual(case, alias, key, {'vinculo': binding, 'firmante': signer,
                'estado': 'PRESENTADA', 'comprobaciones': {}, 'antecedentes': sorted(antecedents)})
            if antecedents:
                _auditar_contractual(case, alias, 'revision_renovada', key,
                    {'antecedentes': sorted(antecedents), 'pdfSha256': binding['pdfSha256']})
        payload = {'vinculoSha256': _digest_contractual(binding)}
        events = _auditorias_contractuales(case,alias,'credihoy_contractual_presentado',key)
        if events:
            _exigir_auditoria_contractual(case, alias, 'presentado', key, payload)
        else:
            _auditar_contractual(case, alias, 'presentado', key, payload)
        token = signing.dumps(binding, key=signing_key(), salt=CONTRACTUAL_SALT)
    return {'solicitudId': str(case.pk), 'operacionFirmaId': key, 'documentoOperacionId': doc,
        'codigo': meta['codigo'], 'pdfSha256': meta['pdfSha256'], 'pdfBase64': base64.b64encode(pdf).decode(),
        'presentacion': token, 'proposito': CONTRACTUAL_PURPOSE, 'ttl': CONTRACTUAL_TTL}


def aceptar_documento_contractual(solicitud_id, identity, *, presentacion, acepta, clave):
    from django.core import signing
    from .publico_seguimiento import key as signing_key
    if acepta is not True:
        raise ValueError('ACEPTACION_CONTRACTUAL_EXPRESA_REQUERIDA')
    key = str(UUID(str(clave)))
    try:
        binding = signing.loads(presentacion, key=signing_key(), salt=CONTRACTUAL_SALT,
                               max_age=CONTRACTUAL_TTL, fallback_keys=[])
    except Exception:
        raise ValueError('PRESENTACION_CONTRACTUAL_INVALIDA_O_VENCIDA') from None
    with _transaccion_contractual(solicitud_id, identity) as (case, alias):
        record = _leer_contractual(case, key)
        if record['vinculo'] != binding:
            raise ValueError('PRESENTACION_CONTRACTUAL_DE_OTRO_ACTO')
        _vinculo_sesion_contractual(record, identity)
        if binding['solicitudId'] != str(case.pk) or binding['operacionFirmaId'] != key:
            raise ValueError('PRESENTACION_CONTRACTUAL_DE_OTRO_ENVIO')
        pdf, meta = _documento_contractual(case, binding['documentoOperacionId'])
        signer = record['firmante']
        if (binding['pdfSha256'] != meta['pdfSha256'] or binding['firmanteSha256'] != _digest_contractual(signer)
                or binding['codigo'] != meta['codigo'] or binding['versionDocumento'] != meta['version']):
            raise ValueError('DOCUMENTO_PRESENTADO_CAMBIO')
        _exigir_auditoria_contractual(case, alias, 'presentado', key, {'vinculoSha256': _digest_contractual(binding)})
        history = case.consumo_detail.orchestration_data.get(CONTRACTUAL_HISTORY, {})
        if record['estado'] != 'PRESENTADA':
            existing = _leer_contractual(case, key)
            if existing['vinculo'] != binding:
                raise ValueError('ACEPTACION_CONTRACTUAL_IDEMPOTENCIA_CONFLICTO')
            return _estado_contractual(existing)
        for other in history:
            if other != key and _leer_contractual(case, other)['vinculo']['pdfSha256'] == binding['pdfSha256']:
                if not puede_renovar_revision_contractual(case, alias, _leer_contractual(case, other), other):
                    raise ValueError('DOCUMENTO_CONTRACTUAL_YA_RECLAMADO')
        record.update(firmante=signer, aceptacionId=str(uuid4()), aceptadoEn=timezone.now().isoformat(),
                      estado='ACEPTADA_PENDIENTE_OTP', comprobaciones={})
        record['aceptacionSha256'] = _digest_contractual({k: record[k] for k in (
            'vinculo', 'firmante', 'aceptacionId', 'aceptadoEn')})
        _guardar_contractual(case, alias, key, record)
        _auditar_contractual(case, alias, 'aceptado', key, {'aceptacionSha256': record['aceptacionSha256']})
        return _estado_contractual(record)


def _estado_contractual(record):
    return {'solicitudId': record['vinculo']['solicitudId'], 'operacionFirmaId': record['vinculo']['operacionFirmaId'],
        'estado': record['estado'], 'proposito': CONTRACTUAL_PURPOSE, 'firmaProveedorVerificada': False}


def _challenge_contractual(case, alias, record, *, lock=False):
    from apps.xcore_consumo.models import OtpChallenge
    query = OtpChallenge.objects.using(alias)
    if lock:
        query = query.select_for_update()
    challenge = query.get(pk=record['otpId'], solicitud_id=case.pk)
    if (challenge.provider != 'twilio_verify' or challenge.max_intentos != CONTRACTUAL_MAX_ATTEMPTS
            or not challenge.validation_result.startswith('contractual:') or challenge.otp_hash
            or challenge.codigo or challenge.otp_code_encrypted or challenge.otp_masked):
        raise ValueError('CHALLENGE_DE_OTRO_PROPOSITO_O_NO_INTEGRO')
    return challenge


def _cliente_otp_contractual(service,*,identity=None,record=None,comprobacion_id=None):
    import logging
    from apps.xcore_consumo.services.twilio_verify import TwilioVerifyClient
    if any(logging.getLogger(name).isEnabledFor(logging.INFO) for name in ('twilio', 'twilio.http_client')):
        raise ValueError('OTP_CONTRACTUAL_LOG_HTTP_NO_AISLADO')
    if getattr(settings,'CREDIHOY_OTP_CONTRACTUAL_TRANSPORTE','SDK')=='RELE_HTTP':
        from .publico_institucional import cliente_verify_contractual
        return cliente_verify_contractual(service,identity,record,comprobacion_id=comprobacion_id)
    key_sid = getattr(settings, 'TWILIO_VERIFY_API_KEY_SID', '')
    key_secret = getattr(settings, 'TWILIO_VERIFY_API_KEY_SECRET', '')
    account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', '')
    if (any(type(value) is not str for value in (key_sid, key_secret, account_sid))
            or not re.fullmatch('SK[a-fA-F0-9]{32}', key_sid.strip())
            or len(key_secret.strip()) < 32
            or not re.fullmatch('AC[a-fA-F0-9]{32}', account_sid.strip())):
        raise ValueError('PERFIL_VERIFY_CONTRACTUAL_DEDICADO_REQUERIDO')
    return TwilioVerifyClient(service_sid=service)


def enviar_otp_contractual(solicitud_id, identity, *, clave):
    from apps.xcore_consumo.services import otp
    if getattr(settings, 'CREDIHOY_OTP_CONTRACTUAL_ENVIO_ENABLED', False) is not True:
        raise ValueError('ENVIO_OTP_CONTRACTUAL_NO_HABILITADO')
    service = getattr(settings, 'CREDIHOY_OTP_CONTRACTUAL_VERIFY_SID', '')
    if (type(service) is not str or not re.fullmatch('VA[a-fA-F0-9]{32}', service)
            or service in (getattr(settings, 'CREDIHOY_PUBLICO_VERIFY_SID', None), getattr(settings, 'TWILIO_VERIFY_SID', None))):
        raise ValueError('SERVICIO_VERIFY_CONTRACTUAL_DEDICADO_REQUERIDO')
    key = str(UUID(str(clave)))
    with _transaccion_contractual(solicitud_id, identity) as (case, alias):
        record = _leer_contractual(case, key)
        _vinculo_sesion_contractual(record, identity)
        _documento_contractual(case, record['vinculo']['documentoOperacionId'])
        if record['estado'] in {'OTP_ENVIADA', 'OTP_VALIDADA'}:
            return _estado_contractual(record)
        if record['estado'] != 'ACEPTADA_PENDIENTE_OTP':
            raise ValueError('OTP_CONTRACTUAL_INCIERTA_NO_REENVIAR')
        if timezone.now()-_fecha_contractual(record['aceptadoEn']) > timedelta(seconds=CONTRACTUAL_TTL):
            raise ValueError('ACEPTACION_CONTRACTUAL_VENCIDA_PARA_ENVIO')
        destination = otp.normalize_sms_destination(case.solicitante.celular)
        challenge = otp._create_base_challenge(case, channel='SMS', provider=otp.PROVIDER_TWILIO_VERIFY,
            destination=destination, using=alias, max_intentos=CONTRACTUAL_MAX_ATTEMPTS,
            validation_result='contractual:send_reserved')
        now = timezone.now()
        challenge.validation_result = 'contractual:send_reserved'
        challenge.expira_at = now+timedelta(seconds=CONTRACTUAL_TTL)
        challenge.save(using=alias, update_fields=['validation_result', 'expira_at', 'updated_at'])
        record.update(estado='OTP_ENVIO_RESERVADO', otpId=str(challenge.pk), serviceSid=service,
            reservadoEn=now.isoformat(), contactoSha256=hashlib.sha256(destination.encode()).hexdigest())
        _guardar_contractual(case, alias, key, record)
        _auditar_contractual(case, alias, 'otp_reservado', key, {'otpId': str(challenge.pk), 'proposito': CONTRACTUAL_PURPOSE})
    # No database transaction or caller code survives the provider boundary.
    try:
        reply = _cliente_otp_contractual(service,identity=identity,record=record).start_verification(destination, channel='sms')
        sid = getattr(reply, 'sid', None)
        if not re.fullmatch('VE[a-fA-F0-9]{32}', sid or '') or getattr(reply, 'status', None) != 'pending':
            raise ValueError
        success = True
    except Exception:
        success, sid = False, ''
    result = _cerrar_envio_otp_contractual(case, alias, key, success, sid)
    if not success:
        raise ValueError('OTP_CONTRACTUAL_ENVIO_INDETERMINADO_NO_REPETIR') from None
    return result


def verificar_otp_contractual(solicitud_id, identity, *, clave, codigo, clave_verificacion):
    if getattr(settings, 'CREDIHOY_OTP_CONTRACTUAL_ENVIO_ENABLED', False) is not True:
        raise ValueError('VERIFICACION_OTP_CONTRACTUAL_NO_HABILITADA')
    if type(codigo) is not str or not re.fullmatch('[0-9]{6}', codigo):
        raise ValueError('CODIGO_CONTRACTUAL_INVALIDO')
    key, operation = str(UUID(str(clave))), str(UUID(str(clave_verificacion)))
    with _transaccion_contractual(solicitud_id, identity) as (case, alias):
        record = _leer_contractual(case, key)
        _vinculo_sesion_contractual(record, identity)
        challenge = _challenge_contractual(case, alias, record, lock=True)
        previous = record['comprobaciones'].get(operation)
        if record['estado'] == 'OTP_VALIDADA':
            _validar_prueba_contractual(case, key, record['vinculo']['pdfSha256'], alias)
            return _estado_contractual(record)
        if previous is not None or record['estado'] != 'OTP_ENVIADA':
            raise ValueError('COMPROBACION_CONTRACTUAL_EXISTENTE_O_INCIERTA_NO_REPETIR')
        now = timezone.now()
        if (challenge.estado != EstadoOtp.ENVIADA or challenge.intentos >= CONTRACTUAL_MAX_ATTEMPTS
                or not challenge.expira_at or now >= challenge.expira_at
                or not re.fullmatch('VE[a-fA-F0-9]{32}', challenge.verification_sid)):
            raise ValueError('OTP_CONTRACTUAL_NO_DISPONIBLE')
        challenge.intentos += 1
        challenge.validation_result = 'contractual:check_reserved'
        challenge.save(using=alias, update_fields=['intentos', 'validation_result', 'updated_at'])
        record['estado'] = 'OTP_COMPROBACION_RESERVADA'
        record['comprobaciones'][operation] = {'estado': 'RESERVADA', 'reservadoEn': now.isoformat()}
        _guardar_contractual(case, alias, key, record)
        _auditar_contractual(case, alias, 'otp_check_reservado', operation, {'otpId': str(challenge.pk), 'envioId': key})
        sid, service = challenge.verification_sid, record['serviceSid']
        destination = decrypt_text(challenge.destination_full_encrypted)
    try:
        reply = _cliente_otp_contractual(service,identity=identity,record=record,comprobacion_id=operation).check_verification(destination, codigo, verification_sid=sid)
        if getattr(reply, 'sid', None) != sid:
            raise ValueError
        status = getattr(reply, 'status', None)
        if status not in {'approved', 'pending'}:
            raise ValueError
    except Exception:
        status = 'uncertain'
    finally:
        codigo = None
    with transaction.atomic(using=alias, durable=True):
        case.consumo_detail = SolicitudConsumo.objects.using(alias).select_for_update().get(solicitud_id=case.pk)
        record = _leer_contractual(case, key)
        challenge = _challenge_contractual(case, alias, record, lock=True)
        if record['estado'] != 'OTP_COMPROBACION_RESERVADA' or challenge.verification_sid != sid:
            raise ValueError('RESERVA_COMPROBACION_CONTRACTUAL_CAMBIO')
        now = timezone.now()
        if now >= min(challenge.expira_at, _fecha_contractual(record['vinculo']['sesionExpiraAt'])):
            status = 'expired'
        record['comprobaciones'][operation]['estado'] = status
        if status == 'approved':
            challenge.estado, challenge.verificado_at = EstadoOtp.VALIDADA, now
            challenge.verification_check_sid = sid
            record['estado'] = 'OTP_VALIDADA'
            proof = {**record['vinculo'], 'aceptacionId': record['aceptacionId'], 'aceptadoEn': record['aceptadoEn'],
                'aceptacionSha256': record['aceptacionSha256'], 'otpId': str(challenge.pk), 'canal': challenge.canal,
                'provider': challenge.provider, 'transactionUuid': challenge.transaction_uuid,
                'serviceSid': service, 'verificationSid': sid, 'verificationCheckSid': sid,
                'contactoSha256': record['contactoSha256'], 'verificadoEn': now.isoformat(), 'checkOperacionId': operation}
            proof['referencia'] = _mac_contractual('proof', proof)
            proof['secureKeySha256'] = hashlib.sha256(_secure_key_contractual(_base_prueba_contractual(proof)).encode()).hexdigest()
            record['prueba'] = proof
            _auditar_contractual(case, alias, 'otp_validado', key,
                {'referencia': proof['referencia'], 'secureKeySha256': proof['secureKeySha256'], 'otpId': str(challenge.pk)})
        elif status == 'pending':
            challenge.estado = EstadoOtp.BLOQUEADA if challenge.intentos >= CONTRACTUAL_MAX_ATTEMPTS else EstadoOtp.ENVIADA
            record['estado'] = 'OTP_BLOQUEADA' if challenge.intentos >= CONTRACTUAL_MAX_ATTEMPTS else 'OTP_ENVIADA'
        else:
            challenge.estado = EstadoOtp.EXPIRADA if status == 'expired' else EstadoOtp.ERROR_ENVIO
            record['estado'] = 'OTP_EXPIRADA' if status == 'expired' else 'OTP_COMPROBACION_INDETERMINADA'
        challenge.validation_result = 'contractual:'+status
        challenge.save(using=alias, update_fields=['estado', 'verificado_at', 'verification_check_sid', 'validation_result', 'updated_at'])
        _guardar_contractual(case, alias, key, record)
    if status != 'approved':
        raise ValueError('OTP_CONTRACTUAL_NO_VALIDADA_NO_REINTENTO_CIEGO') from None
    return _estado_contractual(record)


def _validar_prueba_contractual(case, key, pdf_sha256, alias, *, detail=None):
    try:
        record = _leer_contractual(case, key, detail=detail)
        binding, proof = record['vinculo'], record['prueba']
        pdf, meta = _documento_contractual(case, binding['documentoOperacionId'], detail=detail, historico=True)
        challenge = _challenge_contractual(case, alias, record)
        base = _base_prueba_contractual(proof)
        expected_acceptance = _digest_contractual({k: record[k] for k in ('vinculo', 'firmante', 'aceptacionId', 'aceptadoEn')})
        accepted, checked = _fecha_contractual(proof['aceptadoEn']), _fecha_contractual(proof['verificadoEn'])
        if (record['estado'] != 'OTP_VALIDADA' or proof['version'] != CONTRACTUAL_WORKFLOW
                or any(proof[k] != v for k, v in binding.items()) or proof['proposito'] != CONTRACTUAL_PURPOSE
                or proof['scope'] != CONTRACTUAL_SCOPE or proof['solicitudId'] != str(case.pk)
                or proof['operacionFirmaId'] != key or proof['pdfSha256'] != pdf_sha256
                or meta['pdfSha256'] != pdf_sha256 or proof['codigo'] != meta['codigo']
                or proof['versionDocumento'] != meta['version']
                or proof['originalSha256'] != meta['originalSha256']
                or proof['titularReferencia'] != hashlib.sha256(case.solicitante.numero_identificacion.encode()).hexdigest()
                or proof['firmanteSha256'] != _digest_contractual(record['firmante'])
                or expected_acceptance != record['aceptacionSha256'] or proof['aceptacionSha256'] != expected_acceptance
                or proof['aceptacionId'] != record['aceptacionId'] or proof['aceptadoEn'] != record['aceptadoEn']
                or challenge.estado != EstadoOtp.VALIDADA or challenge.validation_result != 'contractual:approved'
                or challenge.verificado_at != checked or not 1 <= challenge.intentos <= CONTRACTUAL_MAX_ATTEMPTS
                or proof['otpId'] != str(challenge.pk) or proof['provider'] != challenge.provider
                or proof['canal'] != challenge.canal or proof['transactionUuid'] != challenge.transaction_uuid
                or not challenge.verification_sid or challenge.verification_check_sid != challenge.verification_sid
                or proof['verificationSid'] != challenge.verification_sid or proof['verificationCheckSid'] != challenge.verification_sid
                or proof['serviceSid'] != record['serviceSid']
                or proof['contactoSha256'] != hashlib.sha256(decrypt_text(challenge.destination_full_encrypted).encode()).hexdigest()
                or not accepted <= _fecha_contractual(record['reservadoEn']) <= challenge.enviado_at <= checked < challenge.expira_at
                or checked > _fecha_contractual(binding['sesionExpiraAt']) or checked > timezone.now()
                or not hmac.compare_digest(proof['referencia'], _mac_contractual('proof', base))
                or not hmac.compare_digest(proof['secureKeySha256'], hashlib.sha256(_secure_key_contractual(base).encode()).hexdigest())):
            raise ValueError
        _exigir_auditoria_contractual(case, alias, 'presentado', key, {'vinculoSha256': _digest_contractual(binding)})
        _exigir_auditoria_contractual(case, alias, 'aceptado', key, {'aceptacionSha256': expected_acceptance})
        _exigir_auditoria_contractual(case, alias, 'otp_reservado', key, {'otpId': str(challenge.pk), 'proposito': CONTRACTUAL_PURPOSE})
        _exigir_auditoria_contractual(case, alias, 'otp_enviado', key, {'otpId': str(challenge.pk), 'proposito': CONTRACTUAL_PURPOSE})
        _exigir_auditoria_contractual(case, alias, 'otp_check_reservado', proof['checkOperacionId'], {'otpId': str(challenge.pk), 'envioId': key})
        _exigir_auditoria_contractual(case, alias, 'otp_validado', key,
            {'referencia': proof['referencia'], 'secureKeySha256': proof['secureKeySha256'], 'otpId': str(challenge.pk)})
        return dict(proof)
    except Exception:
        raise ValueError('ACEPTACION_CONTRACTUAL_OTP_NO_ACREDITADA') from None

CONTRACTUAL_WORKFLOW = 'CREDIHOY_OTP_CONTRACTUAL_V1'

CONTRACTUAL_PURPOSE = 'FIRMA_DOCUMENTO_CONTRACTUAL'

CONTRACTUAL_SCOPE = 'solicitudes:firmarContrato'

CONTRACTUAL_HISTORY = 'credihoy_otp_contractual_v1'

CONTRACTUAL_TTL = 600

CONTRACTUAL_MAX_ATTEMPTS = 3

CONTRACTUAL_SALT = 'credihoy-documento-contractual-v1'


def _auditorias_contractuales(case,alias,event,key):
    if alias=='cotizacion':
        from django.db import connections
        with connections[alias].cursor() as cursor:
            cursor.execute('SELECT payload FROM public.credihoy_auditoria_contractual_publica '
                'WHERE solicitud_id=%s AND event=%s AND request_id=%s LIMIT 2',[str(case.pk),event,str(key)])
            return [json.loads(row[0]) if type(row[0]) is str else row[0] for row in cursor.fetchall()]
    return list(AuditLog.objects.using(alias).filter(solicitud_id=case.pk,event=event,request_id=key).values_list('payload',flat=True)[:2])



def operar_contractual(identity,action):
    """Personal operations over the existing authorization transport, never a server acceptance."""
    from .publico_institucional import alcance_contractual
    required={'operacion','accion','solicitudId','clave'}
    extras={'contractual_presentar':set(),'contractual_aceptar':{'presentacion','acepta'},
        'contractual_enviar_otp':set(),'contractual_comprobar_otp':{'codigo','comprobacionId'},
        'contractual_firmar':{'confirmaFirma'},'contractual_consultar':set()}
    if (type(action) is not dict or action.get('operacion')!='autorizacion'
            or action.get('accion') not in extras or set(action)!=required|extras[action['accion']]):
        raise ValueError('OPERACION_CONTRACTUAL_INVALIDA')
    sid,key=str(UUID(action['solicitudId'])),str(UUID(action['clave']))
    scope=alcance_contractual(sid,identity)
    name=action['accion'];document=None;pending=[];signed=None
    independent_verified=False
    if name=='contractual_presentar':
        document=presentar_documento_contractual(sid,identity,documento_operacion_id=scope['documentoOperacionId'],clave=key)
    elif name=='contractual_aceptar':
        aceptar_documento_contractual(sid,identity,presentacion=action['presentacion'],acepta=action['acepta'],clave=key)
    elif name=='contractual_enviar_otp':enviar_otp_contractual(sid,identity,clave=key)
    elif name=='contractual_comprobar_otp':
        verificar_otp_contractual(sid,identity,clave=key,codigo=action['codigo'],clave_verificacion=action['comprobacionId'])
    case,alias=_contexto_contractual(sid,identity)
    record=_leer_contractual(case,key)
    expired_review=name in {'contractual_consultar','contractual_presentar'} and puede_renovar_revision_contractual(case,alias,record,key)
    if not expired_review:_vinculo_sesion_contractual(record,identity)
    if record['vinculo']['documentoOperacionId']!=scope['documentoOperacionId'] or record['vinculo']['pdfSha256']!=scope['pdfSha256']:
        raise ValueError('DOCUMENTO_CONTRACTUAL_CAMBIO')
    if expired_review:pending=['REVISION_PERSONAL_CONTRACTUAL_VENCIDA']
    if name=='contractual_consultar' and not expired_review:
        from django.core import signing
        from .publico_seguimiento import key as signing_key
        pdf,meta=_documento_contractual(case,scope['documentoOperacionId'])
        _exigir_auditoria_contractual(case,alias,'presentado',key,{'vinculoSha256':_digest_contractual(record['vinculo'])})
        document={'solicitudId':sid,'operacionFirmaId':key,'documentoOperacionId':scope['documentoOperacionId'],
            'codigo':meta['codigo'],'pdfSha256':meta['pdfSha256'],'pdfBase64':base64.b64encode(pdf).decode(),
            'presentacion':signing.dumps(record['vinculo'],key=signing_key(),salt=CONTRACTUAL_SALT),
            'proposito':CONTRACTUAL_PURPOSE,'ttl':CONTRACTUAL_TTL}
    history=case.consumo_detail.orchestration_data.get('credihoy_autentic_envios_v1',{})
    if name=='contractual_firmar' or name=='contractual_consultar' and key in history:
        from dataclasses import replace
        from apps.documentos.formularios_credihoy import enviar_formato_custodiado,recuperar_documento_autentic,inspeccionar_evidencia_custodiada
        from apps.integraciones.autentic.cliente import AutenticDEVClient
        from apps.integraciones.autentic.contratos import ConfiguracionSTAGING
        from apps.integraciones.autentic.operaciones import RegistroFirmasExpediente
        if name=='contractual_firmar' and action['confirmaFirma'] is not True:
            raise ValueError('CONFIRMACION_PERSONAL_CONTRACTUAL_REQUERIDA')
        if name=='contractual_firmar':
            with _transaccion_contractual(sid,identity) as (locked,locked_alias):
                current=_leer_contractual(locked,key);_vinculo_sesion_contractual(current,identity)
                _validar_prueba_contractual(locked,key,scope['pdfSha256'],locked_alias)
                if 'confirmacionFirma' not in current:
                    current['confirmacionFirma']={'confirmadaEn':timezone.now().isoformat(),
                        'requestId':identity['_requestId'],'pdfSha256':scope['pdfSha256'],'ambiente':'STAGING'}
                    _guardar_contractual(locked,locked_alias,key,current)
                    _auditar_contractual(locked,locked_alias,'firma_confirmada',key,current['confirmacionFirma'])
        config=ConfiguracionSTAGING.desde_archivo(getattr(settings,'CREDIHOY_AUTENTIC_STAGING_PROFILE_FILE',''))
        client=AutenticDEVClient(replace(config,permitir_documentos=True))
        reference='CREDIHOY_CONTRACTUAL_STAGING:'+sid+':'+key
        try:
            if name=='contractual_firmar':
                enviar_formato_custodiado(sid,actor=None,titular=identity,operacion_id=scope['documentoOperacionId'],
                    metadata=None,clave_idempotencia=key,cliente=client,
                    registro=RegistroFirmasExpediente(sid,key,alias=alias),
                    referencia_autorizacion=reference,autoriza_firma=True)
            case,alias=_contexto_contractual(sid,identity)
            entry=case.consumo_detail.orchestration_data['credihoy_autentic_envios_v1'][key]
            if entry.get('vinculo',{}).get('ambienteFirma')!='STAGING':raise ValueError('ENVIO_CONTRACTUAL_DE_OTRO_AMBIENTE')
            if not entry.get('reciboCifrado'):
                pending=[{'FALLO_DEFINITIVO_DOCUMENTADO':'FIRMA_RECHAZADA_DOCUMENTADA_NO_REENVIAR',
                    'CONCILIADA_NO_PROCESADA':'FIRMA_NO_PROCESADA_CONCILIADA_NO_REENVIAR'}.get(
                        entry['estado'],'RESPUESTA_PROVEEDOR_INCIERTA_NO_REENVIAR')]
            else:
                pdf,download=recuperar_documento_autentic(sid,actor=None,titular=identity,envio_id=key,
                    cliente=client,referencia_autorizacion=reference)
                signed={'pdfBase64':base64.b64encode(pdf).decode(),'pdfSha256':download['pdfSha256']}
                try:
                    result=inspeccionar_evidencia_custodiada(sid,actor=None,titular=identity,envio_id=key)
                    pending=result.get('pendientes',[])
                    independent_verified=bool(scope.get('documentoPrueba') and result.get('firmaAcreditada') is True)
                except (ImportError,ModuleNotFoundError):pending=['VERIFICADOR_DOCUMENTAL_NO_DISPONIBLE_EN_RUNTIME']
        finally:client.close()
    case,alias=_contexto_contractual(sid,identity)
    record=_leer_contractual(case,key)
    sent=case.consumo_detail.orchestration_data.get('credihoy_autentic_envios_v1',{}).get(key)
    if getattr(settings,'CREDIHOY_OTP_CONTRACTUAL_ENVIO_ENABLED',False) is not True:
        pending.append('TRANSPORTE_OTP_CONTRACTUAL_NO_HABILITADO')
    if getattr(settings,'CREDIHOY_AUTENTIC_CONTRACTUAL_STAGING_ENVIO_ENABLED',False) is not True:
        pending.append('TRANSPORTE_FIRMA_STAGING_NO_HABILITADO')
    return {'versionContrato':'credihoy-contractual-staging-v1','solicitudId':sid,'operacionFirmaId':key,
        'ambiente':'STAGING','estado':'PRUEBA_FIRMA_VERIFICADA' if independent_verified else sent['estado'] if sent else record['estado'],
        'pdfSha256':scope['pdfSha256'],'documento':document,'firmado':signed,'pendientes':pending,
        'otpProveedor':'TWILIO_VERIFY','otpVerificado':record['estado']=='OTP_VALIDADA',
        'firmaProductivaAcreditada':False,'autorizaDesembolso':False}



def puede_renovar_revision_contractual(case, alias, record, key):
    """Allow a new personal review only after an expired act without a signature claim."""
    claims = case.consumo_detail.orchestration_data.get('credihoy_autentic_envios_v1', {})
    if any(entry.get('vinculo', {}).get('pdfSha256') == record['vinculo']['pdfSha256'] for entry in claims.values()):
        return False
    if record['estado'] == 'OTP_VALIDADA' and record.get('confirmacionFirma'):
        if getattr(settings, 'CREDIHOY_CONTRACTUAL_AMBIENTE', '') != 'STAGING':
            return False
        if _digest_contractual(record) != _digest_contractual(_leer_contractual(case, key)):
            return False
        proof = _validar_prueba_contractual(case, key, record['vinculo']['pdfSha256'], alias)
        _, meta = _documento_contractual(case, record['vinculo']['documentoOperacionId'], historico=True)
        challenge = _challenge_contractual(case, alias, record)
        confirmation = record['confirmacionFirma']
        _exigir_auditoria_contractual(case, alias, 'firma_confirmada', key, confirmation)
        end = min(challenge.expira_at, _fecha_contractual(record['vinculo']['sesionExpiraAt']))
        return (meta.get('ambienteFirma') == 'STAGING' and meta.get('firmaProductivaAcreditada') is False
            and confirmation.get('ambiente') == 'STAGING' and confirmation.get('pdfSha256') == proof['pdfSha256']
            and _fecha_contractual(proof['verificadoEn']) <= _fecha_contractual(confirmation['confirmadaEn']) < end
            and timezone.now() >= end+timedelta(seconds=60))
    recovered_expiry = record['estado'] == 'OTP_EXPIRADA' and bool(record.get('recuperacionEnvio'))
    if recovered_expiry:
        challenge = _challenge_contractual(case, alias, record)
        if (challenge.estado != EstadoOtp.EXPIRADA or challenge.validation_result != 'contractual:sent_expired'
                or challenge.intentos != 0 or challenge.verification_check_sid
                or not re.fullmatch('VE[a-fA-F0-9]{32}', challenge.verification_sid)
                or not challenge.expira_at or timezone.now() < challenge.expira_at+timedelta(seconds=60)):
            return False
        _exigir_auditoria_contractual(case, alias, 'otp_envio_recuperado', key, record['recuperacionEnvio'])
        _exigir_auditoria_contractual(case, alias, 'otp_enviado', key, {'otpId': record['otpId'], 'proposito': CONTRACTUAL_PURPOSE})
    if (record['estado'] not in {'PRESENTADA', 'ACEPTADA_PENDIENTE_OTP'}
            and not recovered_expiry
            or record.get('comprobaciones') != {}
            or any(k in record for k in (('prueba', 'confirmacionFirma') if recovered_expiry else ('otpId', 'serviceSid', 'prueba', 'confirmacionFirma')))
            or key in case.consumo_detail.orchestration_data.get('credihoy_autentic_envios_v1', {})):
        return False
    binding = record['vinculo']
    if (binding['proposito'] != CONTRACTUAL_PURPOSE or binding['scope'] != CONTRACTUAL_SCOPE
            or binding['titularReferencia'] != hashlib.sha256(case.solicitante.numero_identificacion.encode()).hexdigest()):
        return False
    now = timezone.now()
    expired = _fecha_contractual(binding['sesionExpiraAt']) <= now
    if record['estado'] == 'ACEPTADA_PENDIENTE_OTP' or recovered_expiry:
        if record.get('aceptacionSha256') != _digest_contractual({k: record[k] for k in (
                'vinculo', 'firmante', 'aceptacionId', 'aceptadoEn')}):
            raise ValueError('ACEPTACION_CONTRACTUAL_NO_INTEGRA')
        _exigir_auditoria_contractual(case, alias, 'aceptado', key, {'aceptacionSha256': record['aceptacionSha256']})
        expired = expired or now-_fecha_contractual(record['aceptadoEn']) > timedelta(seconds=CONTRACTUAL_TTL)
    if not expired:
        return False
    _exigir_auditoria_contractual(case, alias, 'presentado', key, {'vinculoSha256': _digest_contractual(binding)})
    if alias == 'cotizacion':
        from django.db import connections
        with connections[alias].cursor() as cursor:
            cursor.execute('SELECT event FROM public.credihoy_auditoria_contractual_publica '
                'WHERE solicitud_id=%s AND request_id=%s LIMIT 7', [str(case.pk), key])
            events = [r[0] for r in cursor.fetchall()]
    else:
        events = list(AuditLog.objects.using(alias).filter(solicitud_id=case.pk, request_id=key).values_list('event', flat=True)[:7])
    allowed = {'credihoy_contractual_presentado', 'credihoy_contractual_aceptado', 'credihoy_contractual_revision_renovada'}
    if recovered_expiry:
        allowed |= {'credihoy_contractual_otp_reservado', 'credihoy_contractual_otp_enviado', 'credihoy_contractual_otp_envio_recuperado'}
    return bool(events) and set(events) <= allowed and len(events) <= (6 if recovered_expiry else 3)



def _cerrar_envio_otp_contractual(case, alias, key, success, sid, *, acuse=None):
    with transaction.atomic(using=alias, durable=True):
        case.consumo_detail = SolicitudConsumo.objects.using(alias).select_for_update().get(solicitud_id=case.pk)
        record = _leer_contractual(case, key)
        challenge = _challenge_contractual(case, alias, record, lock=True)
        if acuse is not None and record.get('recuperacionEnvio', {}).get('acuseSha256') == acuse['acuseSha256']:
            return _estado_contractual(record)
        if record['estado'] != 'OTP_ENVIO_RESERVADO':
            raise ValueError('RESERVA_OTP_CONTRACTUAL_CAMBIO')
        if acuse is not None and any(acuse[k] != record[k] for k in ('otpId', 'aceptacionSha256')):
            raise ValueError('ACUSE_OTP_CONTRACTUAL_CAMBIO')
        now = timezone.now()
        expired = success and now >= challenge.expira_at
        challenge.verification_sid = sid
        challenge.enviado_at = now if success else None
        challenge.estado = EstadoOtp.EXPIRADA if expired else EstadoOtp.ENVIADA if success else EstadoOtp.ERROR_ENVIO
        challenge.validation_result = 'contractual:sent_expired' if expired else 'contractual:sent' if success else 'contractual:send_uncertain'
        challenge.save(using=alias, update_fields=['verification_sid', 'enviado_at', 'estado', 'validation_result', 'updated_at'])
        record['estado'] = 'OTP_EXPIRADA' if expired else 'OTP_ENVIADA' if success else 'OTP_ENVIO_INDETERMINADO'
        if acuse is not None:
            record['recuperacionEnvio'] = dict(acuse)
            _auditar_contractual(case, alias, 'otp_envio_recuperado', key, acuse, actor='sistema:contractual:conciliacion')
        _guardar_contractual(case, alias, key, record)
        _auditar_contractual(case, alias, 'otp_enviado' if success else 'otp_incierto', key,
            {'otpId': str(challenge.pk), 'proposito': CONTRACTUAL_PURPOSE},
            actor='sistema:contractual:conciliacion' if acuse is not None else 'titular:contractual')
    return _estado_contractual(record)



def conciliar_acuse_otp_contractual(solicitud_id, *, clave, acuse):
    """Acknowledge a signed, finalized relay result. Never sends or approves an OTP."""
    from django.core import signing
    from uuid import uuid5
    from .publico_seguimiento import key as shared_key
    key = str(UUID(str(clave)))
    proof = signing.loads(acuse, key=shared_key(), salt='credihoy-contractual-otp-ack-v1', max_age=3600, fallback_keys=[])
    fields = {'version', 'journalId', 'verificationSid', 'estadoProveedor', 'observadoEn', 'otpId', 'pdfSha256',
        'aceptacionSha256', 'contactoSha256', 'serviceSha256', 'aceptadoEn', 'solicitudId', 'operacionFirmaId'}
    scope = getattr(settings, 'CREDIHOY_CONTRACTUAL_STAGING_ALCANCE', {})
    if (set(proof) != fields or proof['version'] != 'CREDIHOY_CONTRACTUAL_OTP_ACK_V1'
            or proof['estadoProveedor'] != 'pending' or proof['solicitudId'] != str(solicitud_id)
            or proof['operacionFirmaId'] != key or scope.get('solicitudId') != str(solicitud_id)
            or scope.get('ambiente') != 'STAGING' or scope.get('pdfSha256') != proof['pdfSha256']
            or not re.fullmatch('VE[a-fA-F0-9]{32}', proof['verificationSid'])):
        raise ValueError('ACUSE_OTP_CONTRACTUAL_DE_OTRA_OPERACION')
    alias = settings.CREDIHOY_OTP_CONTRACTUAL_DB_ALIAS
    case = Solicitud.objects.using(alias).select_related('solicitante').get(pk=solicitud_id, producto=PRODUCTO, asesor__isnull=True)
    case.consumo_detail = SolicitudConsumo.objects.using(alias).get(solicitud_id=case.pk)
    record = _leer_contractual(case, key)
    if (any(proof[k] != record[k] for k in ('otpId', 'aceptacionSha256', 'contactoSha256', 'aceptadoEn'))
            or proof['pdfSha256'] != record['vinculo']['pdfSha256']
            or record['vinculo']['documentoOperacionId'] != scope['documentoOperacionId']
            or scope['titularReferencia'] != hashlib.sha256(case.solicitante.numero_identificacion.encode()).hexdigest()
            or proof['serviceSha256'] != hashlib.sha256(record['serviceSid'].encode()).hexdigest()
            or proof['journalId'] != str(uuid5(UUID(key), 'CONTRACTUAL:ENVIAR:'+str(record['otpId'])))
            or not _fecha_contractual(record['reservadoEn']) <= _fecha_contractual(proof['observadoEn']) <= timezone.now()):
        raise ValueError('ACUSE_OTP_CONTRACTUAL_NO_CONCORDANTE')
    _exigir_auditoria_contractual(case, alias, 'aceptado', key, {'aceptacionSha256': record['aceptacionSha256']})
    _exigir_auditoria_contractual(case, alias, 'otp_reservado', key, {'otpId': record['otpId'], 'proposito': CONTRACTUAL_PURPOSE})
    meta = {'acuseSha256': hashlib.sha256(acuse.encode()).hexdigest(), 'journalId': proof['journalId'],
        'otpId': proof['otpId'], 'aceptacionSha256': proof['aceptacionSha256'],
        'verificationSidSha256': hashlib.sha256(proof['verificationSid'].encode()).hexdigest(),
        'observadoEn': proof['observadoEn'], 'conciliadoEn': timezone.now().isoformat()}
    previous = record.get('recuperacionEnvio')
    if previous and previous['acuseSha256'] == meta['acuseSha256']:
        return _estado_contractual(record)
    return _cerrar_envio_otp_contractual(case, alias, key, True, proof['verificationSid'], acuse=meta)

