"""Versioned documentary eligibility, independent from infrastructure TTLs."""
VERSION='COLO023_V10_GERPO085_V03_FINALIDAD_V1'
FORM_SHA='01dd898c75e81a7c8e027784a896600f49c0486db9ef7b3ee72fc2290becf20c'
POLICY_SHA='76e019da0891ef0213d646898d81df02d314ba8844ed70ea028adf29c39cd86d'


def sellar(case,binding,result):
    import base64
    from django.conf import settings
    from apps.xcore_consumo.models import ConsentimientoConsumo
    from . import centrales_personal as c
    alias=(getattr(getattr(case.consumo_detail,'_state',None),'db',None)
        or getattr(getattr(case,'_state',None),'db',None)
        or getattr(settings,'CREDIHOY_AUTORIZACION_DB_ALIAS','default'))
    consent=ConsentimientoConsumo.objects.using(alias).get(solicitud=case)
    c._vigencia_consentimiento_documental(case,consent)
    payload={**binding,'operacionEnvioOriginal':case.metadatos['envio']['operacionId'],
        'versionEvidencia':2,'criterioUsoVersion':VERSION,
        'ventanaCapturaHasta':str(settings.CREDIHOY_PUBLICO_AUTORIZACION_HASTA),
        **{k:result[k] for k in c.RESULTADO_KEYS-{'vinculo'}}}
    signature=c._clave_owner().sign(c._mensaje_evidencia(payload))
    c._clave_publica(owner=True).verify(signature,c._mensaje_evidencia(payload))
    return {'algoritmo':'Ed25519','payload':payload,'firmaBase64':base64.b64encode(signature).decode('ascii')}


def verificar_v2(case,consent,journal,*,owner=False):
    import base64
    from django.utils import timezone
    from . import centrales_personal as c
    proof=journal['comprobante'];p=proof['payload']
    expected=c.VINCULO_KEYS|{'operacionEnvioOriginal','versionEvidencia','criterioUsoVersion','ventanaCapturaHasta'}|(c.RESULTADO_KEYS-{'vinculo'})
    if (set(proof)!={'algoritmo','payload','firmaBase64'} or proof['algoritmo']!='Ed25519'
            or type(p) is not dict or set(p)!=expected or type(p['versionEvidencia']) is not int
            or p['versionEvidencia']!=2 or p['criterioUsoVersion']!=VERSION):
        raise ValueError('CENTRALES_COMPROBANTE_INVALIDO')
    c._clave_publica(owner=owner).verify(base64.b64decode(proof['firmaBase64'],validate=True),c._mensaje_evidencia(p))
    binding={k:p[k] for k in c.VINCULO_KEYS};now=timezone.now()
    c._validar_vinculo(binding,now,vigente=False)
    confirmed=c._fecha(p['confirmadoEn'])
    if (journal['version']!=c.VERSION or journal['estado']!='OTP_CONFIRMADA' or journal['vinculo']!=binding
            or consent.firmado is not False or any(p[k]!=v for k,v in c._base(case,consent).items())
            or p['operacionEnvioOriginal']!=case.metadatos['envio']['operacionId']
            or p['estado']!='CONFIRMADA' or p['estadoProveedor']!='approved'
            or not consent.fecha_aceptacion<=c._fecha(binding['aceptadoEn'])<=confirmed<=now
            or confirmed>c._fecha(binding['vigenteHasta']) or confirmed>c._fecha(p['ventanaCapturaHasta'])):
        raise ValueError('CENTRALES_CONFIRMACION_NO_ACREDITADA')
    c._uuid(p['operacionVerify']);c._hash(p['verificationReferencia'])
    until=c._vigencia_consentimiento_documental(case,consent)
    return c.AutorizacionCentralesVerificada(metodo='OTP',referencia_verificacion=p['verificationReferencia'],
        texto_sha256=p['textoSha256'],pdf_sha256=p['pdfSha256'],confirmado_en=confirmed,vigente_hasta=until)


def evaluar(case,consent,*,owner=False):
    from . import centrales_personal as central
    from .autoservicio import validar_vinculo_consumo
    validar_vinculo_consumo(case)
    journal=case.consumo_detail.orchestration_data.get(central.KEY,{})
    # Verify the original signature and confirmation chronology unchanged.
    verified=central._verificar_comprobante(case,consent,journal,vigente=False,owner=owner)
    until=central._vigencia_consentimiento_documental(case,consent)
    return {'version':VERSION,'estado':'APTA_POR_FINALIDAD','referenciaDocumento':'COLO-FO.023:v10',
        'referenciaPolitica':'GER-PO.085:v03:1.2,1.4.7,1.4.9,2.2,2.3,2.4',
        'finalidad':'CONSULTA_CENTRALES_ORIGINACION_MISMO_EXPEDIENTE',
        'confirmadoEn':verified.confirmado_en.isoformat(),
        'restriccionDocumentalHasta':until.isoformat() if until else None,
        'revocacionRegistrada':False,'habilitaConsumo':False}


def validar_consumo(case):
    from datetime import datetime
    from django.utils import timezone
    from .consulta_controlada import validar_politica_del_consumo
    from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
    try:validar_politica_del_consumo(case)
    except OperacionError as exc:return exc.codigo
    approval=case.metadatos.get('autorizacionEconomicaCredihoy') or {}
    if (case.metadatos.get('consumoRealAutorizado') is not True
            or not approval.get('referenciaCostos') or not approval.get('responsableCondiciones')
            or not approval.get('referenciaAutorizacionConsumo')
            or approval.get('maximoConsultasPorProveedor')!=1):
        return 'AUTORIZACION_INSTITUCIONAL_CONSUMO_PENDIENTE'
    if approval.get('proveedores')!=['HDC']:return 'AUTORIZACION_HDC_EXCLUSIVA_REQUERIDA'
    if approval.get('vigenteHasta'):
        try:
            end=datetime.fromisoformat(approval['vigenteHasta'])
            if end.utcoffset() is None or end<=timezone.now():raise ValueError
        except (TypeError,ValueError):return 'AUTORIZACION_INSTITUCIONAL_CONSUMO_PENDIENTE'
    return None
