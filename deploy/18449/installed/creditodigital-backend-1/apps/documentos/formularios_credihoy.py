"""Official AcroForms, not reconstructed documents or electronic signatures."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json

from pypdf import PdfReader, PdfWriter

ASSETS=Path(__file__).resolve().parents[1]/'xcore_consumo'/'assets'
FORMATOS={
    'COLO-FO.001':{'archivo':'COLO-FO.001_v13_20260701.pdf','version':'13','fecha':'2026-07-01',
        'sha256':'93a8f53214a95fdbeb249572f40cac9c751d3b8ac481372e9d11dfa75899cc9e','paginas':3},
    'COLO-FO.023':{'archivo':'COLO-FO.023_v10_20260525.pdf','version':'10','fecha':'2026-05-25',
        'sha256':'01dd898c75e81a7c8e027784a896600f49c0486db9ef7b3ee72fc2290becf20c','paginas':1}}

CLASIFICACION_CREDIHOY={'modalidad':'111','linea':'LIV','nombreLinea':'Libre Inversion','campo':'Check Box14',
    'fuente':'Modalidades.csv','fuenteSha256':'666cd6e3eaf52f0f9a6ed8a3eb28423e5e4c02705017dc20dc6093032d6357ce',
    'correspondenciaPDF':'ETIQUETA_LIBRE_INVERSION_Y549_6_WIDGET_Y547_733_558_594'}

MEDIOS_PAGO={'VENTANILLA':'Check Box25','NOMINA':'Check Box26'}

MAPA={
    'COLO-FO.001':{'sucursal':'Sucursal','ciudad':'Ciudad','nombre':'Nombres y Apellidos o Raz\u00f3n Social',
        'identificacion':'N identificaci\u00f3n','direccion':'Direcci\u00f3n','celular':'Tel\u00e9fono m\u00f3vil',
        'correo':'Direcci\u00f3n de correo electr\u00f3nico','monto':'Monto solicitado','plazo':'Plazo',
        'periodicidad':'Periodicidad','destino':'Destino del cr\u00e9dito','tasaEA':'Tasa de inter\u00e9s EA',
        'primerVencimiento':'Fecha de inicio del plan de pagos Dia','ingresos':'Ingresos','gastos':'Gastos',
        'activos':'Activos','pasivos':'Pasivos','codeudorNombre':'Nombre del codeudor o descripci\u00f3n de la garant\u00eda',
        'codeudorIdentificacion':'N identificacion','codeudorCelular':'Tel\u00e9fono m\u00f3vil_2',
        'codeudorCorreo':'Direcci\u00f3n de correo electr\u00f3nico_2',
        'entidadRecogida':'Nombre de la entidad1','saldoLiquidable':'Saldo a cancelar1',
        'obligacionRecogida':'Numero de la obligaci\u00f3n1','saldoTotalRecogida':'Saldo a cancelarTotal saldo a cancelar',
        'fechaDia':'Texto1','fechaMes':'Texto2','fechaAnio':'Texto3'},
    'COLO-FO.023':{'nombre':'Nombre','identificacion':'CC  NIT','celular':'Telefono',
        'ciudad':'En constancia','fechaDia':'a los','fechaMes':'d\u00edas del mes de','fechaAnio':'del a\u00f1o'}}


def original(codigo):
    spec=FORMATOS[codigo]; data=(ASSETS/spec['archivo']).read_bytes()
    if sha256(data).hexdigest()!=spec['sha256']:raise ValueError('ORIGINAL_DOCUMENTAL_MODIFICADO')
    return data,spec


def inventario_formato(codigo):
    data,spec=original(codigo); reader=PdfReader(BytesIO(data)); fields=[]
    for page_num,page in enumerate(reader.pages,1):
        for ref in page.get('/Annots',[]):
            widget=ref.get_object()
            if widget.get('/Subtype')!='/Widget':continue
            fields.append({'pagina':page_num,'nombre':str(widget.get('/T','')),
                'tipo':str(widget.get('/FT','')),'rect':[str(v) for v in widget.get('/Rect',[])],
                'aparienciaDisponible':bool(widget.get('/AP')),'aparienciaDefault':str(widget.get('/DA','')),
                'estados':list(widget.get('/AP',{}).get('/N',{})) if str(widget.get('/FT'))=='/Btn' else []})
    return {**spec,'codigo':codigo,'campos':fields,
        'observaciones':['PAGINACION_IMPRESA_1_DE_1_EN_ORIGINAL_DE_3_PAGINAS'] if len(reader.pages)==3 else []}


def _diligenciar(codigo, campos, *, seleccion_comunicaciones=None, perfil=None):
    """Internal mapping input: each value has its own verified provenance.

    No health/insurance declarations, guarantees, product checkbox or signature
    is guessed. Rendering is deliberately separate from acceptance and sending.
    """
    data,spec=original(codigo); mapping=MAPA[codigo]; values={}; evidence={}
    extra={'medioPago'} if codigo=='COLO-FO.001' else set()
    if type(campos) is not dict or set(campos)-set(mapping)-extra:raise ValueError('CAMPO_NO_MAPEADO_NO_AUTOCOMPLETAR')
    for key,item in campos.items():
        if (type(item) is not dict or set(item)!={'valor','referencia','verificado'} or item['verificado'] is not True
                or type(item['valor']) is not str or not item['referencia'] or len(item['valor'])>300
                or any(ord(c)<32 for c in item['valor'])):raise ValueError('DATO_DOCUMENTAL_NO_ACREDITADO')
        if key=='medioPago':
            if item['valor'] not in MEDIOS_PAGO:raise ValueError('MEDIO_PAGO_NO_ACREDITADO')
            for option,field in MEDIOS_PAGO.items():
                values[field]='/S\u00ed' if option==item['valor'] else '/Off'
        else:values[mapping[key]]=item['valor']
        evidence[key]=item['referencia']
    classification=None
    if codigo=='COLO-FO.001' and perfil is not None:
        if perfil!=('390','111','CFA'):raise ValueError('CLASIFICACION_DOCUMENTAL_DE_OTRO_PRODUCTO')
        classification=CLASIFICACION_CREDIHOY
        values.update({'Check Box14':'/S\u00ed','Check Box15':'/Off','Check Box16':'/Off','Check Box17':'/Off'})
        evidence['lineaCredito']=classification['fuenteSha256']
    if codigo=='COLO-FO.023' and seleccion_comunicaciones is not None:
        choice=seleccion_comunicaciones
        if (type(choice) is not dict or set(choice)!={'valor','referenciaAceptacionTitular'}
                or type(choice['valor']) is not bool or not choice['referenciaAceptacionTitular']):
            raise ValueError('COMUNICACIONES_REQUIERE_SELECCION_PERSONAL')
        yes='Est\u00e1 de acuerdo con que nos comuniquemos a trav\u00e9s de los medios y en los horarios antes indicados Si'
        values.update({yes:'X' if choice['valor'] else '', 'No':'' if choice['valor'] else 'X'})
        evidence['comunicaciones']=choice['referenciaAceptacionTitular']
    reader=PdfReader(BytesIO(data));writer=PdfWriter();writer.clone_document_from_reader(reader)
    if len(writer.pages)!=spec['paginas']:raise ValueError('PAGINAS_OFICIALES_NO_COINCIDEN')
    for page in writer.pages:
        writer.update_page_form_field_values(page,values,auto_regenerate=False)
    output=BytesIO();writer.write(output); rendered=output.getvalue()
    return rendered,{'versionContrato':'credihoy-pdf-oficial-diligenciado-v1','codigo':codigo,
        'version':spec['version'],'originalSha256':spec['sha256'],'pdfSha256':sha256(rendered).hexdigest(),
        'identidadDocumentalReferencia':sha256(campos['identificacion']['valor'].encode()).hexdigest()
            if 'identificacion' in campos else None,
        'evidenciasPorCampo':evidence,'camposDiligenciados':sorted(values),'contenidoNormativoModificado':False,
        'firmaAcreditada':False,'envioHabilitado':False,'clasificacionProducto':classification,
        'pendientes':(['CLASIFICACION_LINEA_PRODUCTO'] if classification is None else [])+
            ['DECLARACIONES_PERSONALES_ASEGURABILIDAD','ACEPTACION_TITULAR']
            if codigo=='COLO-FO.001' else ['ACEPTACION_CENTRALES_Y_FIRMA_TITULAR'],
        'observaciones':inventario_formato(codigo)['observaciones']}


def _contexto_formato(solicitud_id,actor,titular,permiso,*,bloquear=False):
    if titular is None:
        from .services import _permiso_documental,_expediente_documental
        _permiso_documental(actor,permiso)
        return _expediente_documental(solicitud_id,actor,bloquear=bloquear),'default',str(actor.pk)
    if actor is not None:raise ValueError('NO_MEZCLAR_ACTOR_Y_TITULAR')
    from apps.xcore_consumo.services.credihoy_v2.unico.consentimiento_real import obtener_autogestion
    case,alias=obtener_autogestion(solicitud_id,titular,lock=bloquear)
    return case,alias,'titular:'+titular['_sesionReferencia']


def diligenciar_para_expediente(solicitud_id, *, actor, codigo, campos, seleccion_comunicaciones=None,titular=None):
    case,_,_=_contexto_formato(solicitud_id,actor,titular,'view_versiondocumental')
    if titular is not None and codigo!='COLO-FO.023':raise ValueError('CANAL_TITULAR_SOLO_COLO023')
    if case.producto!='CREDIHOY UNICO (NUEVO)':raise ValueError('FORMATO_DE_OTRO_PRODUCTO')
    quote=_validar_condiciones_en_campos(case,campos) if codigo=='COLO-FO.001' else None
    pdf,evidence=_diligenciar(codigo,campos,seleccion_comunicaciones=seleccion_comunicaciones,
        perfil=('390','111','CFA'))
    if evidence['identidadDocumentalReferencia'] not in (None,sha256(case.solicitante.numero_identificacion.encode()).hexdigest()):
        raise ValueError('IDENTIFICACION_DOCUMENTAL_DE_OTRO_EXPEDIENTE')
    evidence['solicitudId']=str(case.pk)
    if quote is not None:
        evidence['composicionSha256']=quote['composicionSha256']
        offer=quote.get('ofertaVersionada')
        if offer is not None:
            pdf,link=_anexar_oferta_versionada(case,pdf,quote,campos)
            evidence.update(link,pdfSha256=sha256(pdf).hexdigest())
    # Bytes go to existing private documentary custody, never a public asset/log.
    return pdf,evidence


def preparar_autentic_para_expediente(solicitud_id, *, actor, codigo, campos, metadata,
        seleccion_comunicaciones=None):
    if codigo==PDF_ENSAYO:raise ValueError('PDF_SIMULACION_NO_ADMITE_FIRMA')
    """Reuse Autentic's actual request builder, without sending or certifying it.

    Separate stages: central authorization precedes HDC; the credit form needs
    definitive financial conditions. LINPAG's promissory note never enters here.
    """
    from .services import _permiso_documental,_expediente_documental
    from apps.integraciones.autentic.contratos import preparar_firma
    _permiso_documental(actor,'view_versiondocumental')
    case=_expediente_documental(solicitud_id,actor)
    if type(metadata) is not dict or metadata.get('docId')!=case.solicitante.numero_identificacion:
        raise ValueError('FIRMANTE_DE_OTRO_EXPEDIENTE')
    if codigo=='COLO-FO.001':
        _validar_condiciones_en_campos(case,campos)
    pdf,evidence=diligenciar_para_expediente(solicitud_id,actor=actor,codigo=codigo,
        campos=campos,seleccion_comunicaciones=seleccion_comunicaciones)
    request=preparar_firma(metadata,{codigo+'.pdf':pdf})
    return request,{**evidence,'solicitudAutenticFingerprint':request.fingerprint,'proveedor':'AUTENTIC',
        'etapa':'PRECONSULTA' if codigo=='COLO-FO.023' else 'FORMALIZACION',
        'paquetesDeOtraEtapaNoAgrupados':True,'pagareIncluido':False,'envioHabilitado':False}


def _exigir_condiciones_definitivas(case):
    """Native offer conditions do not require our own amortization schedule."""
    from uuid import uuid4
    from apps.xcore_consumo.services.credihoy_v2.unico.cotizacion import leer_cotizacion_guardada,leer_oferta_linix,exigir_calendario_formalizacion
    try:
        offer=leer_oferta_linix(case)
    except (ValueError,KeyError,AttributeError):offer=None
    if offer:
        extra=offer['condicionesAdicionales']
        if extra['tasaEaPorcentaje'] is not None and extra['primeraFechaPago'] is not None:
            exigir_calendario_formalizacion(case,offer)
            return {'solicitudId':str(case.pk),'versionCondiciones':offer['versionCondiciones'],
                'condicionesSolicitadas':offer['condiciones'],'tasaEaPorcentaje':extra['tasaEaPorcentaje']['valor'],
                'primeraFechaPago':extra['primeraFechaPago']['valor'],'composicionSha256':offer['sha256'],
                'cotizacionIntegral':False,'planPagosGestionadoPorLINIX':True,'ofertaVersionada':offer}
    quote=leer_cotizacion_guardada(case,str(uuid4()))
    if quote.get('cotizacionIntegral') is not True:
        from datetime import datetime
        from django.utils import timezone
        from apps.xcore_consumo.models import SolicitudConsumo
        history=SolicitudConsumo.objects.get(solicitud=case).orchestration_data.get(
            'credihoy_composiciones_contractuales_v1',{})
        matches=[]
        for value in history.values():
            body={k:v for k,v in value.items() if k!='sha256'}
            from apps.xcore_consumo.services.credihoy_v2.unico.condiciones_producto import CALENDARIO_VERSION
            calendar=value.get('preparacionCalendario',{})
            if (value.get('solicitudId')!=str(case.pk)
                    or calendar.get('version')!=CALENDARIO_VERSION or calendar.get('coincideFechaNativa') is not True
                    or value.get('versionCondiciones')!=quote.get('versionCondiciones')
                    or value.get('requestId')!=quote.get('calculoRequestId',quote.get('requestId'))
                    or value.get('sha256')!=sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()):
                continue
            try:when=datetime.fromisoformat(value['fechaConsulta'])
            except (KeyError,TypeError,ValueError):continue
            if when.utcoffset() is None or not 0<=(timezone.now()-when).total_seconds()<=300:continue
            if value.get('titularReferencia')!=sha256(case.solicitante.numero_identificacion.encode()).hexdigest():continue
            matches.append(value)
        if matches and quote.get('disponible') is True:
            latest=max(matches,key=lambda v:v['fechaConsulta'])
            quote={**quote,'cotizacionIntegral':True,'calendario':{'disponible':True,'cuotas':latest['calendario']},
                'primeraFechaPago':latest['calendario'][0]['fecha'],'seguroVida':{'disponible':True},
                'tasaEaPorcentaje':latest['tasaEaPorcentaje'],'composicionSha256':latest['sha256']}
    if (quote.get('disponible') is not True or quote.get('cotizacionIntegral') is not True
            or quote.get('solicitudId')!=str(case.pk) or not quote.get('versionCondiciones')
            or not quote.get('composicionSha256')
            or quote.get('calendario',{}).get('disponible') is not True
            or quote.get('seguroVida',{}).get('disponible') is not True):
        raise ValueError('COLO001_REQUIERE_CONDICIONES_CONTRACTUALES_DEFINITIVAS')
    return quote


def _validar_condiciones_en_campos(case,campos):
    from decimal import Decimal,InvalidOperation
    quote=_exigir_condiciones_definitivas(case)
    try:
        expected={'monto':quote['condicionesSolicitadas']['monto'],
            'plazo':str(quote['condicionesSolicitadas']['numeroCuotas']),
            'tasaEA':quote['tasaEaPorcentaje']}
        if (any(Decimal(campos[k]['valor'])!=Decimal(v) for k,v in expected.items())
                or campos['periodicidad']['valor'].upper()!=quote['condicionesSolicitadas']['periodicidad']
                or campos['primerVencimiento']['valor']!=quote['primeraFechaPago']):raise ValueError()
    except (KeyError,TypeError,ValueError,InvalidOperation):
        raise ValueError('COLO001_CAMPOS_NO_CORRESPONDEN_A_CONDICIONES') from None
    return quote


def enviar_formato_custodiado(solicitud_id, *, actor, operacion_id, metadata,
        clave_idempotencia, cliente, registro, referencia_autorizacion):
    """Bind the existing DEV sender to encrypted dossier custody, never certify a signature."""
    from uuid import UUID
    from dataclasses import asdict
    from django.db import connection,transaction
    from django.utils import timezone
    from apps.xcore_consumo.models import SolicitudConsumo
    from apps.xcore_consumo.services.otp_crypto import encrypt_text
    from apps.integraciones.autentic.cliente import AutenticDEVClient,ReciboFirma
    from apps.integraciones.autentic.contratos import AutenticError,preparar_firma
    from apps.integraciones.autentic.operaciones import RegistroFirmas
    from .services import _permiso_documental,_expediente_documental
    if connection.in_atomic_block:raise ValueError('ENVIO_REQUIERE_TRANSACCION_INDEPENDIENTE')
    _permiso_documental(actor,'add_eventodocumental')
    if (not isinstance(cliente,AutenticDEVClient) or not isinstance(registro,RegistroFirmas)
            or not referencia_autorizacion):raise ValueError('CONTRATO_ENVIO_AUTENTIC_NO_ACREDITADO')
    cliente._documentos_autorizados(referencia_autorizacion)
    key=str(UUID(str(clave_idempotencia)))
    pdf,document=recuperar_formato(solicitud_id,actor=actor,operacion_id=operacion_id)
    if document.get('codigo')==PDF_ENSAYO:raise ValueError('PDF_SIMULACION_NO_ADMITE_FIRMA')
    if document.get('anexoOferta',{}).get('homologacionAcreditada') is False:raise ValueError('ANEXO_OFERTA_PENDIENTE_HOMOLOGACION')
    request=preparar_firma(metadata,{document['codigo']+'.pdf':pdf})
    history_key='credihoy_autentic_envios_v1'
    with transaction.atomic():
        case=_expediente_documental(solicitud_id,actor,bloquear=True)
        if metadata.get('docId')!=case.solicitante.numero_identificacion:raise ValueError('FIRMANTE_DE_OTRO_EXPEDIENTE')
        if document.get('identidadDocumentalReferencia')!=sha256(metadata['docId'].encode()).hexdigest():
            raise ValueError('IDENTIDAD_EN_PDF_NO_ACREDITADA')
        if document['codigo']=='COLO-FO.001':
            quote=_exigir_condiciones_definitivas(case)
            if not document.get('composicionSha256') or document['composicionSha256']!=quote.get('composicionSha256'):
                raise ValueError('COLO001_COMPOSICION_CAMBIO')
        detail=SolicitudConsumo.objects.select_for_update().get(solicitud=case)
        data=detail.orchestration_data;history=dict(data.get(history_key,{}))
        binding={'solicitudId':str(case.pk),'documentoOperacionId':str(operacion_id),
            'codigo':document['codigo'],'pdfSha256':document['pdfSha256'],
            'firmanteReferencia':sha256(metadata['docId'].encode()).hexdigest(),
            'fingerprint':request.fingerprint,'referenciaAutorizacion':referencia_autorizacion}
        if key in history:
            if history[key]['vinculo']!=binding:raise ValueError('IDEMPOTENCIA_DOCUMENTO_CONFLICTO')
            return {'operacionId':key,'estado':history[key]['estado'],'firmaAcreditada':False,
                'reenvioPermitido':False}
        if any(v['vinculo']['pdfSha256']==document['pdfSha256'] for v in history.values()):
            raise ValueError('DOCUMENTO_YA_RECLAMADO_CONCILIAR')
        history[key]={'vinculo':binding,'estado':'ENVIANDO','actorId':str(actor.pk),
            'preparadoEn':timezone.now().isoformat()}
        detail.orchestration_data={**data,history_key:history}
        detail.save(update_fields=['orchestration_data','updated_at'])
    # The durable dossier claim precedes network; no SQL transaction spans HTTP.
    state='INDETERMINADA';encrypted=None
    try:
        receipt=cliente.firmar(request,clave=key,registro=registro,
            referencia_autorizacion=referencia_autorizacion)
        if not isinstance(receipt,ReciboFirma):raise AutenticError('RECIBO_AUTENTIC_NO_RECUPERADO')
        encrypted=encrypt_text(json.dumps(asdict(receipt),sort_keys=True))
        state='FIRMA_REPORTADA_NO_VERIFICADA'
    except Exception:
        pass
    with transaction.atomic():
        detail=SolicitudConsumo.objects.select_for_update().get(solicitud_id=solicitud_id)
        history=dict(detail.orchestration_data[history_key])
        history[key]={**history[key],'estado':state,'reciboCifrado':encrypted,
            'finalizadoEn':timezone.now().isoformat()}
        detail.orchestration_data={**detail.orchestration_data,history_key:history}
        detail.save(update_fields=['orchestration_data','updated_at'])
    return {'operacionId':key,'estado':state,'firmaAcreditada':False,'reenvioPermitido':False}


def recuperar_documento_autentic(solicitud_id, *, actor, envio_id, cliente, referencia_autorizacion):
    """Download through the documented receipt URL; bytes/hash are not proof of signing."""
    import base64
    from uuid import UUID
    from django.db import connection,transaction
    from django.utils import timezone
    from apps.xcore_consumo.models import SolicitudConsumo
    from apps.xcore_consumo.services.otp_crypto import encrypt_text,decrypt_text
    from apps.integraciones.autentic.cliente import AutenticDEVClient,ReciboFirma
    from .services import _permiso_documental,_expediente_documental
    if connection.in_atomic_block:raise ValueError('DESCARGA_REQUIERE_TRANSACCION_INDEPENDIENTE')
    _permiso_documental(actor,'add_eventodocumental')
    case=_expediente_documental(solicitud_id,actor)
    if not isinstance(cliente,AutenticDEVClient):raise ValueError('CLIENTE_AUTENTIC_NO_ACREDITADO')
    key=str(UUID(str(envio_id)));history_key='credihoy_autentic_envios_v1'
    entry=SolicitudConsumo.objects.get(solicitud=case).orchestration_data[history_key][key]
    binding=entry['vinculo']
    _,original_meta=recuperar_formato(solicitud_id,actor=actor,operacion_id=binding['documentoOperacionId'])
    if (binding['solicitudId']!=str(case.pk) or binding['pdfSha256']!=original_meta['pdfSha256']
            or binding['firmanteReferencia']!=sha256(case.solicitante.numero_identificacion.encode()).hexdigest()):
        raise ValueError('VINCULO_DOCUMENTAL_NO_INTEGRO')
    if entry.get('descargaCifrada'):
        envelope=json.loads(decrypt_text(entry['descargaCifrada']))
    else:
        if entry['estado']!='FIRMA_REPORTADA_NO_VERIFICADA' or not entry.get('reciboCifrado'):
            raise ValueError('ENVIO_INDETERMINADO_CONCILIAR')
        receipt=ReciboFirma(**json.loads(decrypt_text(entry['reciboCifrado'])))
        name=binding['codigo']+'.pdf'
        files=cliente.recuperar_documentos(receipt,[name],referencia_autorizacion=referencia_autorizacion)
        file=files[name]
        envelope={'solicitudId':str(case.pk),'envioId':key,'originalSha256':binding['pdfSha256'],
            'transaccionReferencia':sha256(receipt.transaccion.encode()).hexdigest(),
            'pdfSha256':file.sha256,'pdfBase64':base64.b64encode(file.contenido).decode('ascii'),
            'recuperadoEn':timezone.now().isoformat(),'firmaAcreditada':False,
            'correspondenciaContenidoFirmadoVerificada':False}
        with transaction.atomic():
            _expediente_documental(solicitud_id,actor,bloquear=True)
            detail=SolicitudConsumo.objects.select_for_update().get(solicitud=case)
            history=dict(detail.orchestration_data[history_key])
            if history[key]['vinculo']!=binding:raise ValueError('VINCULO_DOCUMENTAL_CAMBIO')
            if history[key].get('descargaCifrada'):
                previous=json.loads(decrypt_text(history[key]['descargaCifrada']))
                if previous['pdfSha256']!=envelope['pdfSha256']:raise ValueError('DOCUMENTO_PROVEEDOR_CAMBIO')
                envelope=previous
            else:
                history[key]={**history[key],'descargaCifrada':encrypt_text(json.dumps(envelope,sort_keys=True))}
                detail.orchestration_data={**detail.orchestration_data,history_key:history}
                detail.save(update_fields=['orchestration_data','updated_at'])
    pdf=base64.b64decode(envelope['pdfBase64'],validate=True)
    if (envelope['solicitudId']!=str(case.pk) or envelope['envioId']!=key
            or envelope['originalSha256']!=binding['pdfSha256']
            or sha256(pdf).hexdigest()!=envelope['pdfSha256']):
        raise ValueError('DESCARGA_DOCUMENTAL_NO_INTEGRA')
    return pdf,{k:v for k,v in envelope.items() if k!='pdfBase64'}


def custodiar_formato(solicitud_id, *, actor, codigo, campos, clave_idempotencia,
        seleccion_comunicaciones=None,titular=None):
    """Encrypted private bytes in the existing dossier; no new table or public URL."""
    from uuid import UUID
    from django.db import transaction
    _,alias,actor_ref=_contexto_formato(solicitud_id,actor,titular,'add_eventodocumental')
    if titular is not None and codigo!='COLO-FO.023':raise ValueError('CANAL_TITULAR_SOLO_COLO023')
    key=str(UUID(str(clave_idempotencia)))
    with transaction.atomic(using=alias):
        case,_,_=_contexto_formato(solicitud_id,actor,titular,'add_eventodocumental',bloquear=True)
        quote=_validar_condiciones_en_campos(case,campos) if codigo=='COLO-FO.001' else None
        pdf,meta=diligenciar_para_expediente(case.pk,actor=actor,codigo=codigo,campos=campos,
            seleccion_comunicaciones=seleccion_comunicaciones,titular=titular)
        if quote is not None:meta['composicionSha256']=quote.get('composicionSha256')
        return _custodiar_pdf(case,actor=actor,titular=titular,alias=alias,actor_ref=actor_ref,
            key=key,pdf=pdf,meta=meta,event='CREDIHOY_PDF_OFICIAL_CUSTODIADO')


def recuperar_formato(solicitud_id, *, actor, operacion_id,titular=None):
    import base64
    from uuid import UUID
    from apps.xcore_consumo.models import SolicitudConsumo
    from apps.xcore_consumo.services.otp_crypto import decrypt_text
    case,alias,_=_contexto_formato(solicitud_id,actor,titular,'view_versiondocumental')
    key=str(UUID(str(operacion_id)))
    from apps.utils.models import AuditLog
    trial=AuditLog.objects.using(alias).filter(solicitud=case,
        event='CREDIHOY_PDF_ENSAYO_SIMULACION_CUSTODIADO',request_id=key)
    if trial.count()>1:raise ValueError('CUSTODIA_DOCUMENTAL_DUPLICADA')
    if trial.exists():
        if titular is not None:raise ValueError('CANAL_TITULAR_SOLO_COLO023')
        stored=trial.get().payload
        envelope=json.loads(decrypt_text(stored['contenidoCifrado']))
        pdf=base64.b64decode(envelope['pdfBase64'],validate=True)
        if (envelope['solicitudId']!=str(case.pk) or envelope['operacionId']!=key
                or envelope['metadata']!=stored['metadata'] or stored['codigo']!=PDF_ENSAYO
                or stored['metadata']['codigo']!=PDF_ENSAYO
                or sha256(pdf).hexdigest()!=stored['metadata']['pdfSha256']
                or stored['sha256']!=stored['metadata']['pdfSha256']):
            raise ValueError('CUSTODIA_DOCUMENTAL_NO_INTEGRA')
        return pdf,dict(stored['metadata'])
    detail=SolicitudConsumo.objects.using(alias).get(solicitud=case)
    stored=detail.orchestration_data['credihoy_pdf_oficiales_v1'][key]
    if titular is not None and stored['metadata']['codigo']!='COLO-FO.023':raise ValueError('CANAL_TITULAR_SOLO_COLO023')
    envelope=json.loads(decrypt_text(stored['contenidoCifrado']))
    pdf=base64.b64decode(envelope['pdfBase64'],validate=True)
    if (envelope['solicitudId']!=str(case.pk) or envelope['operacionId']!=key
            or envelope['metadata']!=stored['metadata'] or sha256(pdf).hexdigest()!=stored['metadata']['pdfSha256']):
        raise ValueError('CUSTODIA_DOCUMENTAL_NO_INTEGRA')
    return pdf,dict(stored['metadata'])

def _anexar_oferta_versionada(case,pdf,quote,campos):
    """Local traceable copy of stored conditions, not a new contract or signature."""
    from xml.sax.saxutils import escape
    from apps.utils.pdf import build_legal_document_pdf
    offer=quote['ofertaVersionada']
    if (offer['solicitudId']!=str(case.pk) or offer['sha256']!=quote['composicionSha256']
            or offer['titularReferencia']!=sha256(case.solicitante.numero_identificacion.encode()).hexdigest()
            or offer['versionCondiciones']!=quote['versionCondiciones']):
        raise ValueError('OFERTA_DOCUMENTAL_DE_OTRO_CONTEXTO')
    rows=[
        'Expediente: '+str(case.pk),
        'Titular: '+campos.get('nombre',{}).get('valor','Nombre pendiente de acreditacion'),
        'Identificacion: '+case.solicitante.numero_identificacion,
        'Oferta SHA-256: '+offer['sha256'],
        'Version de condiciones: '+offer['versionCondiciones'],
        'Calculo institucional: '+offer['calculoRequestId'],
        'Monto COP: '+offer['condiciones']['monto'],
        'Plazo en meses: '+str(offer['condiciones']['numeroCuotas']),
        'Periodicidad: '+offer['condiciones']['periodicidad'],
        'Tasa NAMV porcentaje: '+offer['condiciones']['tasaNominalNamv'],
        'Tasa EA porcentaje: '+quote['tasaEaPorcentaje'],
        'Cuota institucional COP (sin redondeo adicional): '+offer['cuotaContractual'],
        'Primer vencimiento: '+quote['primeraFechaPago'],
        'Vigencia de oferta: '+offer['desde']+' a '+offer['hasta'],
        'Seguro mensual parametrizado porcentaje: '+offer['seguroVida']['porcentajeMensualParametrizado'],
        'Importe del seguro: gestionado por LINIX; no se suma otra prima desde el canal.',
        'Intereses iniciales: descuento en desembolso segun LINIX. Plan de pagos: gestion nativa LINIX.',
        'No acredita aceptacion, firma, pagare, radicacion ni desembolso.',
        'Anexo local de trazabilidad; homologacion documental pendiente antes de su envio contractual.']
    annex=build_legal_document_pdf('Condiciones de oferta versionada CrediHoy',
        'Copia local vinculada al COLO-FO.001. No sustituye el plan de pagos institucional.',
        [escape(row) for row in rows],deterministic=True)
    writer=PdfWriter();writer.clone_document_from_reader(PdfReader(BytesIO(pdf)))
    original_pages=len(writer.pages)
    for page in PdfReader(BytesIO(annex)).pages:writer.add_page(page)
    output=BytesIO();writer.write(output)
    return output.getvalue(),{'ofertaSha256':offer['sha256'],'versionCondiciones':offer['versionCondiciones'],
        'calculoRequestId':offer['calculoRequestId'],'evaluacionSha256':offer['evaluacionSha256'],
        'anexoOferta':{'version':'credihoy-anexo-oferta-local-v1','sha256':sha256(annex).hexdigest(),
            'paginasOriginales':original_pages,'paginasAnexo':len(writer.pages)-original_pages,
            'homologacionAcreditada':False},'envioHabilitado':False}


def _custodiar_pdf(case,*,actor,titular,alias,actor_ref,key,pdf,meta,event):
    """Shared storage; callers hold the dossier transaction and owner lock."""
    import base64
    from django.db import connections
    from django.utils import timezone
    from apps.xcore_consumo.models import SolicitudConsumo
    from apps.xcore_consumo.services.otp_crypto import encrypt_text
    from apps.utils.models import AuditLog
    if not connections[alias].in_atomic_block:raise ValueError('CUSTODIA_REQUIERE_TRANSACCION')
    if meta['codigo']==PDF_ENSAYO:
        history=AuditLog.objects.using(alias).filter(solicitud=case,event=event,request_id=key)
        if history.count()>1:raise ValueError('CUSTODIA_DOCUMENTAL_DUPLICADA')
        if history.exists():
            entry=history.get()
            if entry.payload['metadata']!=meta or entry.actor!=actor_ref:
                raise ValueError('IDEMPOTENCIA_DOCUMENTO_CONFLICTO')
            recuperar_formato(case.pk,actor=actor,operacion_id=key)
            return {**meta,'operacionId':key}
        envelope={'solicitudId':str(case.pk),'operacionId':key,'metadata':meta,
            'pdfBase64':base64.b64encode(pdf).decode('ascii')}
        AuditLog.objects.using(alias).create(solicitud=case,event=event,request_id=key,actor=actor_ref,
            payload={'codigo':PDF_ENSAYO,'version':meta['version'],'sha256':meta['pdfSha256'],
                'metadata':meta,'contenidoCifrado':encrypt_text(json.dumps(envelope,sort_keys=True))})
        return {**meta,'operacionId':key}
    detail=SolicitudConsumo.objects.using(alias).select_for_update().get(solicitud=case)
    history=dict(detail.orchestration_data.get('credihoy_pdf_oficiales_v1',{}))
    if key in history:
        previous=history[key]['metadata']
        if previous!=meta or history[key]['actorId']!=actor_ref:
            raise ValueError('IDEMPOTENCIA_DOCUMENTO_CONFLICTO')
        recuperar_formato(case.pk,actor=actor,operacion_id=key,titular=titular)
        return {**previous,'operacionId':key}
    envelope={'solicitudId':str(case.pk),'operacionId':key,'metadata':meta,
        'pdfBase64':base64.b64encode(pdf).decode('ascii')}
    history[key]={'metadata':meta,'actorId':actor_ref,'creadoEn':timezone.now().isoformat(),
        'contenidoCifrado':encrypt_text(json.dumps(envelope,sort_keys=True))}
    detail.orchestration_data={**detail.orchestration_data,'credihoy_pdf_oficiales_v1':history}
    detail.save(using=alias,update_fields=['orchestration_data','updated_at'])
    AuditLog.objects.using(alias).create(solicitud=case,event=event,actor=actor_ref,
        request_id=key,payload={'codigo':meta['codigo'],'version':meta['version'],'sha256':meta['pdfSha256']})
    return {**meta,'operacionId':key}


def custodiar_simulacion_ensayo(solicitud_id,*,actor,escenario,clave_idempotencia):
    """Administrative console only. Reuse the simulator; never read a credit offer."""
    from decimal import Decimal
    from uuid import UUID
    import re
    from xml.sax.saxutils import escape
    from django.db import transaction
    from apps.utils.pdf import build_legal_document_pdf
    from apps.xcore_consumo.tasas_models import TasaComercialCredihoy
    from apps.xcore_consumo.services.credihoy_v2.unico import simulacion_lectura as sim,tasas
    from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional import project_simulation
    _,alias,actor_ref=_contexto_formato(solicitud_id,actor,None,'add_eventodocumental')
    # Replay validates private stored bytes through the same read permission.
    from .services import _permiso_documental
    _permiso_documental(actor,'view_versiondocumental')
    key=str(UUID(str(clave_idempotencia)))
    if (type(escenario) is not dict or set(escenario)!={'monto','plazoMeses','linea'}
            or type(escenario['monto']) is not str or not re.fullmatch(r'[0-9]{1,10}(\.[0-9]{1,2})?',escenario['monto'])
            or type(escenario['plazoMeses']) is not int or type(escenario['linea']) is not str
            or escenario['linea'] not in {'VENTANILLA','LIBRANZA'}):raise ValueError('ESCENARIO_ENSAYO_INVALIDO')
    amount,term=Decimal(escenario['monto']),escenario['plazoMeses']
    with transaction.atomic(using=alias):
        case,_,_=_contexto_formato(solicitud_id,actor,None,'add_eventodocumental',bloquear=True)
        if case.producto!='CREDIHOY UNICO (NUEVO)':raise ValueError('FORMATO_DE_OTRO_PRODUCTO')
        limits=sim._limites()
        if (not Decimal(limits['montoMinimo'])<=amount<=Decimal(limits['montoMaximo'])
                or not limits['cuotasMinimas']<=term<=limits['cuotasMaximas']):raise ValueError('ESCENARIO_FUERA_LIMITES')
        cutoff=tasas.hoy()
        tariffs=[t for t in TasaComercialCredihoy.objects.using(alias).filter(perfil__producto='390',
            perfil__modalidad='111',linea=escenario['linea']).select_related('perfil').prefetch_related('eventos')
            if tasas.vigente(t,cutoff) and tasas.condiciones_aprobadas(t)]
        if len(tariffs)!=1:raise ValueError('TARIFA_ACTIVA_NO_UNIVOCA')
        subtotal=sim.orientar_capital_interes(amount,term,tarifa=tariffs[0],fecha=cutoff)
        result=sim.orientar_simulacion(subtotal)
        packet=project_simulation({'versionContrato':'credihoy-simulacion-local-v1',
            'solicitudId':str(case.pk),'requestId':key,'alcance':'ESTIMACION_GENERICA_NO_ELEGIBILIDAD',
            'limites':limits,'tasa':{**subtotal['tasa'],'linea':tariffs[0].linea},
            'seguro':{k:v for k,v in result['seguro'].items() if k not in {'baseCOP','valorMensualEstimado'}},
            'resultado':result,'aviso':sim.AVISO_SIMULACION,'ofertable':False,'autorizaDecision':False,
            'autorizaStart':False,'autorizaDesembolso':False})
        digest=sha256(json.dumps(packet,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        rows=[AVISO_ENSAYO,'No es oferta, aceptacion, contrato, firma, pagare ni autorizacion de desembolso.',
            'Expediente: '+str(case.pk),'Identificacion del titular: '+case.solicitante.numero_identificacion,
            'Referencia de simulacion: '+key,'Version: '+packet['versionContrato'],'Simulacion SHA-256: '+digest,
            'Producto 390 / Modalidad 111 / Destinacion CFA','Corte: '+cutoff.isoformat(),
            'Linea: '+tariffs[0].linea,'Monto COP: '+result['monto'],'Plazo mensual: '+str(term),
            'Tasa NAMV porcentaje: '+packet['tasa']['valor'],'Capital e interes por cuota COP: '+result['cuotaCapitalInteres'],
            'Seguro mensual decimal: '+result['seguro']['tasaMensualDecimal'],
            'Base seguro: CAPITAL INICIAL COP '+result['seguro']['baseCOP'],
            'Seguro mensual estimado COP: '+result['seguro']['valorMensualEstimado'],
            'Cuota estimada COP: '+result['cuotaEstimada'],'Total estimado COP: '+result['totalEstimado'],
            'Base y redondeo del seguro NO conciliados con LINIX. No determina elegibilidad ni capacidad.',
            'Conceptos no incluidos por falta de acreditacion: '+', '.join(result['conceptosNoAcreditados'])]
        if case.metadatos.get('datosSinteticos') is True:
            rows.insert(1,'DATOS SINTETICOS DE PRUEBA. NO ES UN EXPEDIENTE REAL.')
        rows += ['Mes {numero}: capital {capital}; interes {intereses}; seguro estimado {seguroEstimado}; cuota estimada {cuotaEstimada}; saldo {saldoFinal} COP.'.format(**row)
            for row in result['amortizacion']]
        pdf=build_legal_document_pdf(AVISO_ENSAYO,
            'CrediHoy. Valores orientativos; no sustituye el plan de pagos definitivo.',
            [escape(row) for row in rows],deterministic=True,page_notice=AVISO_ENSAYO)
        meta={'versionContrato':'credihoy-pdf-simulacion-ensayo-v1','codigo':PDF_ENSAYO,'version':'1',
            'solicitudId':str(case.pk),'perfil':['390','111','CFA'],'estado':'SIMULACION_NO_CONTRACTUAL',
            'pdfSha256':sha256(pdf).hexdigest(),'simulacionSha256':digest,'simulacion':packet,
            'corte':cutoff.isoformat(),'firmaAcreditada':False,'envioHabilitado':False,
            'ofertable':False,'aceptacionAcreditada':False,'autorizaStart':False,'autorizaDesembolso':False}
        return _custodiar_pdf(case,actor=actor,titular=None,alias=alias,actor_ref=actor_ref,
            key=key,pdf=pdf,meta=meta,event='CREDIHOY_PDF_ENSAYO_SIMULACION_CUSTODIADO')


PDF_ENSAYO='CREDIHOY-SIMULACION-ENSAYO'


AVISO_ENSAYO='SIMULACION - ENSAYO NO CONTRACTUAL'

def preparar_enlace_documental_oferta(solicitud_id, *, actor, etapa='PRECONTRACTUAL'):
    """Read the native offer for its actual stage; never render, accept or emit.

    A precontractual snapshot is not the definitive COLO-FO.001 or a promissory
    note. Only formalization requires the native SC/calendar reconciliation.
    """
    if type(etapa) is not str or etapa not in {'PRECONTRACTUAL','FORMALIZACION'}:
        raise ValueError('ETAPA_DOCUMENTAL_NO_SOPORTADA')
    from apps.xcore_consumo.services.credihoy_v2.unico.cotizacion import (
        leer_oferta_linix,leer_aceptacion_oferta,exigir_calendario_formalizacion)
    case,_,_=_contexto_formato(solicitud_id,actor,None,'view_versiondocumental')
    if case.producto!='CREDIHOY UNICO (NUEVO)':raise ValueError('FORMATO_DE_OTRO_PRODUCTO')
    offer=leer_oferta_linix(case)
    person=sha256(case.solicitante.numero_identificacion.encode()).hexdigest()
    if offer['solicitudId']!=str(case.pk) or offer['titularReferencia']!=person:
        raise ValueError('OFERTA_DOCUMENTAL_DE_OTRO_CONTEXTO')
    accepted=leer_aceptacion_oferta(case)
    if accepted is not None and (
            accepted['ofertaSha256']!=offer['sha256']
            or accepted['versionCondiciones']!=offer['versionCondiciones']
            or accepted['evaluacionSha256']!=offer['evaluacionSha256']
            or accepted['titularReferencia']!=person):
        raise ValueError('ACEPTACION_NO_CORRESPONDE_A_OFERTA')
    if etapa=='FORMALIZACION':
        if accepted is None:raise ValueError('ACEPTACION_PROPIA_REQUERIDA')
        exigir_calendario_formalizacion(case,offer)
    result={'version':'credihoy-enlace-documental-oferta-v1','solicitudId':str(case.pk),
        'titularReferencia':person,'etapa':etapa,'ofertaSha256':offer['sha256'],
        'versionCondiciones':offer['versionCondiciones'],'calculoRequestId':offer['calculoRequestId'],
        'evaluacionSha256':offer['evaluacionSha256'],'condiciones':offer['condiciones'],
        'cuotaContractual':offer['cuotaContractual'],'condicionesAdicionales':offer['condicionesAdicionales'],
        'seguroVida':offer['seguroVida'],'calendarioOferta':offer.get('calendarioPago'),
        'vigenteDesde':offer['desde'],'vigenteHasta':offer['hasta'],
        'aceptacion':None if accepted is None else {
            k:accepted[k] for k in ('sha256','requestId','fecha','ofertaSha256')},
        'requiereScParaEstaLectura':etapa=='FORMALIZACION',
        'documentoDefinitivoGenerado':False,'pagareIncluido':False,'firmaAcreditada':False,
        'envioHabilitado':False,'autorizaStart':False,'autorizaDesembolso':False}
    result=json.loads(json.dumps(result))
    result['sha256']=sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result

