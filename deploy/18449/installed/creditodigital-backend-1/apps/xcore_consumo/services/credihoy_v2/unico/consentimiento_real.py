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


def obtener_autogestion(solicitud_id, identity, *, lock=False):
    """Same submitted dossier, authorized by its holder, never a staff surrogate."""
    from django.db import connections
    from django.http import Http404
    from . import autoservicio
    if (getattr(settings,'CREDIHOY_AUTORIZACION_TITULAR_ENABLED',False) is not True
            or identity.get('_scopeAutorizacion')!='solicitudes:autorizar'):
        raise OperacionError('AUTORIZACION_TITULAR_NO_HABILITADA')
    if str(solicitud_id) not in getattr(settings,'CREDIHOY_AUTORIZACION_SOLICITUDES',()):raise Http404
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
    return case,alias


def presentar_autogestion(solicitud_id,identity,*,campos,clave):
    from django.core import signing
    from apps.documentos.formularios_credihoy import custodiar_formato,recuperar_formato
    import base64
    case,_=obtener_autogestion(solicitud_id,identity)
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
            if (previous.evidencia.get('workflow')!='COLO023_AUTOGESTION_V1'
                    or previous.evidencia.get('pdfSha256')!=meta['pdfSha256']
                    or previous.evidencia.get('claveAceptacion')!=key):
                raise ValueError('AUTORIZACION_EXISTENTE_NO_SOBRESCRIBIR')
            return {'solicitudId':str(case.pk),'estado':previous.evidencia['estado'],
                'firmado':previous.firmado,'consultasHabilitadas':False}
        detail=SolicitudConsumo.objects.using(alias).select_for_update().get(solicitud=case)
        if detail.estado!='CONSENTIMIENTO_PENDIENTE':raise ValueError('ETAPA_AUTORIZACION_NO_DISPONIBLE')
        ConsentimientoConsumo.objects.using(alias).create(solicitud=case,otp=None,aceptado=True,firmado=False,
            version='COLO-FO.023:v'+meta['version'],canal=identity['canal'],fecha_aceptacion=timezone.now(),text_snapshot=text,
            text_hash=build_consent_hash('COLO-FO.023:v'+meta['version'],text),tipo_firma='AUTENTIC_PENDIENTE',
            evidencia={'workflow':'COLO023_AUTOGESTION_V1','estado':'PENDIENTE_FIRMA_AUTENTIC',
                'proposito':binding['proposito'],'pdfSha256':meta['pdfSha256'],'originalSha256':meta['originalSha256'],
                'documentoOperacionId':binding['operacionId'],'claveAceptacion':key,
                'sesionReferencia':identity['_sesionReferencia'],'otp9AReutilizado':False})
        AuditLog.objects.using(alias).create(solicitud=case,event='credihoy_colo023_aceptado_personalmente',
            actor='titular:9A',request_id=key,payload={'codigo':'COLO-FO.023','pdfSha256':meta['pdfSha256'],'firmado':False})
    return {'solicitudId':str(case.pk),'estado':'PENDIENTE_FIRMA_AUTENTIC','firmado':False,'consultasHabilitadas':False}


def preflight_autorizacion_autogestion(solicitud_id,identity):
    case,alias=obtener_autogestion(solicitud_id,identity)
    consent=ConsentimientoConsumo.objects.using(alias).filter(solicitud=case).first()
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
        # The existing signature receipt does not contain documentNumber. Only
        # an authentic reconciliation reply may supply this provider identifier.
        number=entry['documentNumber']
        if type(number) is not str or not number.strip():raise ValueError
        verified=verificar_firma_documental(original=original,firmado=signed,
            transaccion=receipt['transaccion'],document_number=number,
            documento_identidad=case.solicitante.numero_identificacion)
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
    expected={'presentar':base|{'campos','clave'},'aceptar':base|{'presentacion','acepta','clave'},'consultar':base}
    if action not in expected or set(payload)!=expected[action]:raise ValueError('CONTRATO_AUTORIZACION_INVALIDO')
    reference=str(UUID(payload['solicitudId']))
    if reference!=payload['solicitudId'] or identity.get('canal') not in {'SMS','EMAIL'}:raise ValueError('IDENTIDAD_AUTORIZACION_INVALIDA')
    if action=='presentar':
        result=presentar_autogestion(reference,identity,campos=payload['campos'],clave=payload['clave'])
        document={k:result[k] for k in ('codigo','version','pdfSha256','pdfBase64','presentacion','operacionId','proposito')}
    elif action=='aceptar':
        result=aceptar_autogestion(reference,identity,presentacion=payload['presentacion'],acepta=payload['acepta'],clave=payload['clave'])
        document=None
    else:
        result=preflight_autorizacion_autogestion(reference,identity);document=None
    return {'versionContrato':'credihoy-autorizacion-titular-v1','solicitudId':reference,
        'estado':result['estado'],'documento':document,'firmado':False,'consultasHabilitadas':False,
        'evaluacionHabilitada':False}


def preparar_envio_autogestion(solicitud_id,identity,*,metadata,clave,autoriza_firma):
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
    if (autoriza_firma is not True or type(metadata) is not dict or set(metadata)!={'names','lastNames'}
            or any(type(v) is not str or not v.strip() or len(v)>120 for v in metadata.values())):
        raise ValueError('ACTO_PERSONAL_DE_FIRMA_Y_NOMBRES_REQUERIDOS')
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
        'nombresFuente':'DECLARACION_PERSONAL_EN_PDF','identidadFuente':'SESION_9A_Y_TITULARIDAD_VERIFICADAS'}
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
