"""Independent integrity, cryptography and electronic participation evidence."""

from dataclasses import dataclass, field
import base64
from datetime import datetime,timezone
from hashlib import sha256
import hmac
import json
from uuid import UUID
from urllib.parse import urlsplit
import re
from io import BytesIO
import stat
from zipfile import BadZipFile, ZipFile

from .contratos import AutenticError, validar_pdf, _config_unica


MAX_ZIP = 20_000_000


def _procedencia_descarga_integra(procedencia, ambiente, url_sha256=None):
    return (type(procedencia) is dict and ambiente in ('DEV', 'STAGING', 'PRODUCCION')
        and procedencia.get('version') == 'AUTENTIC_DESCARGA_V1'
        and procedencia.get('ambiente') == ambiente
        and type(procedencia.get('http')) is int and procedencia['http'] == 200
        and procedencia.get('TLSVerificado') is True and procedencia.get('sinRedirecciones') is True
        and all(type(procedencia.get(k)) is str and re.fullmatch('[a-f0-9]{64}', procedencia[k])
                for k in ('urlSha256', 'zipSha256'))
        and (url_sha256 is None or procedencia['urlSha256'] == url_sha256))


@dataclass(frozen=True)
class FirmaAutenticVerificada:
    """Internal verifier output, never accepted from a browser/provider JSON flag."""
    transaccion: str
    document_number: str | None
    firmante_sha256: str
    original_sha256: str
    firmado_sha256: str
    firmado_en: datetime
    vigente_hasta: datetime
    referencia_verificacion: str


def analizar_firma_documental(*,original,firmado,transaccion,documento_identidad,
        document_number=None,contexto=None,raices=(),crls=(),ocsps=(),momento=None,politicas_titular=()):
    """Partial, auditable results. A provider certificate is not inferred from a ZIP."""
    result={'version':'AUTENTIC_VERIFICACION_DOCUMENTAL_V1','estado':'PENDIENTE_VERIFICACION',
        'integridadPDF':False,'correlacionExpediente':False,'reciboProveedor':False,
        'procedenciaDescarga':False,'fechaProveedorConZona':False,'firmaCriptografica':False,
        'identidadFirmante':False,'participacionFirmante':False,
        'certificacionPosterior':'PENDIENTE_DOCUMENT_NUMBER_AUTENTICO',
        'firmaAcreditada':False,'pendientes':[],'verificadoEn':(momento or datetime.now(timezone.utc)).isoformat()}
    try:
        result['originalSha256']=validar_pdf(original)
        result['firmadoSha256']=validar_pdf(firmado)
        result['integridadPDF']=True
    except AutenticError:
        result.update(estado='INVALIDA',pendientes=['PDF_INVALIDO']);return result
    if not contexto:
        result['pendientes'].append('CONTEXTO_CUSTODIADO_REQUERIDO')
    else:
        try:
            binding=contexto['vinculo'];download=contexto['descarga'];receipt=contexto['recibo']
            reference=str(UUID(contexto['solicitudId']));send=str(UUID(contexto['envioId']))
            document=str(UUID(contexto['documentoOperacionId']))
            person=sha256(documento_identidad.encode()).hexdigest()
            checks=(binding['solicitudId']==reference,download['solicitudId']==reference,
                download['envioId']==send,binding['documentoOperacionId']==document,
                binding['pdfSha256']==result['originalSha256'],download['originalSha256']==result['originalSha256'],
                download['pdfSha256']==result['firmadoSha256'],hmac.compare_digest(binding['firmanteReferencia'],person),
                download['transaccionReferencia']==sha256(transaccion.encode()).hexdigest())
            if not all(checks):raise ValueError
            result['correlacionExpediente']=True
            reply=receipt.get('respuesta_original',{})
            result['reciboProveedor']=(receipt['transaccion']==transaccion and receipt['codigo_operacion']==1000
                and receipt['estado']=='FIRMA_REPORTADA_NO_VERIFICADA'
                and receipt['tipo_respuesta']=='reply_signature'
                and contexto['estado']=='FIRMA_REPORTADA_NO_VERIFICADA'
                and reply.get('transactionId')==transaccion and reply.get('operationCode')==1000
                and reply.get('processedDate')==receipt['fecha_proveedor']
                and reply.get('type')=='reply_signature' and reply.get('urlDocuments')==receipt['url_documentos'])
            try:
                when=datetime.fromisoformat(receipt['fecha_proveedor'])
                prepared=datetime.fromisoformat(contexto['preparadoEn'])
                recovered=datetime.fromisoformat(download['recuperadoEn'])
                result['fechaProveedorConZona']=(all(v.utcoffset() is not None for v in (when,prepared,recovered))
                    and prepared<=when<=recovered<=(momento or datetime.now(timezone.utc)))
            except (KeyError,ValueError,TypeError):pass
            provenance=download.get('procedencia',{})
            url=urlsplit(receipt['url_documentos'])
            environment = receipt.get('ambiente', 'DEV')
            result['procedenciaDescarga']=(_procedencia_descarga_integra(provenance, environment,
                    sha256(receipt['url_documentos'].encode()).hexdigest())
                and url.scheme=='https' and bool(url.hostname) and not url.username and not url.password
                and not url.fragment and url.port in (None,443))
            result['solicitudId']=reference;result['envioId']=send
        except KeyError:
            result['pendientes'].append('EVIDENCIA_CUSTODIADA_INCOMPLETA');return result
        except (TypeError,ValueError,AttributeError):
            result.update(estado='INVALIDA',pendientes=['VINCULO_O_RECIBO_NO_INTEGRO']);return result
    from .pdf_criptografia import verificar_pdf
    crypto=verificar_pdf(original,firmado,documento_identidad,raices=raices,crls=crls,ocsps=ocsps,
        momento=momento,politicas_titular=politicas_titular)
    result['criptografia']=crypto
    result['firmaCriptografica']=crypto['firmaIntegra'] and crypto['cadenaConfiable']
    # The PDF can be sealed by Autentic or another trusted entity. A certificate
    # subject match is an observation, not the client's personal signing act.
    result['coincidenciaTitularCertificado']=bool(crypto['identidadCertificado'])
    result['rolCertificado']=crypto.get('rolCertificado','NO_DETERMINADO')
    personal=(crypto.get('rolCertificado')=='FIRMANTE_TITULAR'
        and crypto.get('participacionElectronicaAcreditada') is True and crypto['identidadCertificado'])
    result['identidadFirmante']=bool(personal and result['firmaCriptografica'])
    result['participacionFirmante']=result['identidadFirmante']
    if personal and crypto['fechaConfiable']:
        try:
            signed_at=datetime.fromisoformat(crypto['firmadoEn'])
            if not prepared<=signed_at<=recovered:raise ValueError()
        except (KeyError,TypeError,ValueError,UnboundLocalError):
            result['pendientes'].append('FECHA_FIRMA_FUERA_DEL_ENVIO')
    for field in ('correlacionExpediente','reciboProveedor','procedenciaDescarga','fechaProveedorConZona',
            'firmaCriptografica','identidadFirmante','participacionFirmante'):
        if not result[field]:result['pendientes'].append(field)
    if not crypto['correspondenciaOriginal']:result['pendientes'].append('CORRESPONDENCIA_PDF_NO_ACREDITADA')
    if not crypto['fechaConfiable']:result['pendientes'].append('FECHA_FIRMA_NO_ACREDITADA')
    # V4 does not specify a signed identity/transaction/documentNumber mapping.
    # Do not accept a caller-supplied identifier as that missing provider evidence.
    result['documentNumberRecibido']=type(document_number) is str and bool(document_number.strip())
    if result['documentNumberRecibido']:result['certificacionPosterior']='IDENTIFICADOR_RECIBIDO_NO_VERIFICADO'
    if not personal:result['pendientes'].append('EVIDENCIA_IDENTIDAD_Y_PARTICIPACION_PROVEEDOR_PENDIENTE')
    if not result['pendientes']:
        result.update(estado='FIRMA_CONTRACTUAL_VERIFICADA',firmaAcreditada=True,firmadoEn=crypto['firmadoEn'])
    canonical=json.dumps({k:v for k,v in result.items() if k!='verificadoEn'},
        sort_keys=True,separators=(',',':')).encode()
    result['sha256']=sha256(canonical).hexdigest()
    return result


def verificar_firma_documental(*,original,firmado,transaccion,documento_identidad,document_number=None,**kwargs):
    result=analizar_firma_documental(original=original,firmado=firmado,transaccion=transaccion,
        document_number=document_number,documento_identidad=documento_identidad,**kwargs)
    error=AutenticError('VERIFICACION_DOCUMENTAL_INVALIDA' if result['estado']=='INVALIDA' else
        'CONTRATO_VERIFICACION_FIRMANTE_Y_DOCUMENTO_AUTENTIC_PENDIENTE')
    error.resultado=result
    raise error


@dataclass(frozen=True)
class FirmaContractualVerificada:
    solicitud_id: str
    envio_id: str
    original_sha256: str
    firmado_sha256: str
    transaccion_sha256: str
    firmado_en: datetime
    verificacion_sha256: str


def verificar_firma_contractual(*,original,firmado,transaccion,documento_identidad,**kwargs):
    """Contract evidence only; never rewrites a credit consent or its validity."""
    report=analizar_firma_documental(original=original,firmado=firmado,transaccion=transaccion,
        documento_identidad=documento_identidad,**kwargs)
    if report.get('firmaAcreditada') is not True:
        error=AutenticError('FIRMA_CONTRACTUAL_EVIDENCIA_PENDIENTE');error.resultado=report;raise error
    return FirmaContractualVerificada(report['solicitudId'],report['envioId'],report['originalSha256'],
        report['firmadoSha256'],sha256(transaccion.encode()).hexdigest(),
        datetime.fromisoformat(report['firmadoEn']),report['sha256'])


@dataclass(frozen=True)
class EvidenciaPDF:
    sha256: str
    contenido: bytes = field(repr=False)
    firma_criptografica_validada: bool = False

    def __post_init__(self):
        if (self.firma_criptografica_validada is not False
                or self.sha256 != validar_pdf(self.contenido)):
            raise AutenticError('EVIDENCIA_PDF_NO_INTEGRA')


def _vinculo_custodia(vinculo):
    if (type(vinculo) is not dict or set(vinculo) != {
            'solicitudId', 'envioId', 'documentoOperacionId', 'originalSha256',
            'transaccionReferencia', 'ambiente'}):
        raise ValueError
    for key in ('solicitudId', 'envioId', 'documentoOperacionId'):
        if str(UUID(vinculo[key])) != vinculo[key]:
            raise ValueError
    for key in ('originalSha256', 'transaccionReferencia'):
        if type(vinculo[key]) is not str or not re.fullmatch('[a-f0-9]{64}', vinculo[key]):
            raise ValueError
    if vinculo['ambiente'] not in ('DEV', 'STAGING', 'PRODUCCION'):
        raise ValueError


def custodiar_descarga(*, original, evidencia, recibo, vinculo, procedencia, recuperado_en, cifrar):
    """Local encrypted snapshot through the host's cipher; not a provider attestation.

    The caller persists the returned ciphertext exclusively, without overwriting
    the original attempt. This adapter never writes files, journals or APP state.
    """
    try:
        from .cliente import ReciboFirma
        _vinculo_custodia(vinculo)
        if type(recibo) is not ReciboFirma or type(evidencia) is not EvidenciaPDF:
            raise ValueError
        recibo.validar_recuperacion(vinculo['ambiente'])
        if (validar_pdf(original) != vinculo['originalSha256']
                or sha256(recibo.transaccion.encode()).hexdigest() != vinculo['transaccionReferencia']
                or datetime.fromisoformat(recuperado_en).utcoffset() is None
                or not _procedencia_descarga_integra(procedencia, vinculo['ambiente'],
                    sha256(recibo.url_documentos.encode()).hexdigest())):
            raise ValueError
        envelope = {**vinculo, 'version': 'AUTENTIC_CUSTODIA_DESCARGA_LOCAL_V1',
            'pdfSha256': evidencia.sha256, 'pdfBase64': base64.b64encode(evidencia.contenido).decode('ascii'),
            'recuperadoEn': recuperado_en, 'procedencia': dict(procedencia), 'firmaAcreditada': False}
        canonical = json.dumps(envelope, sort_keys=True, separators=(',', ':'))
        encrypted = cifrar(canonical)
        if type(encrypted) is not str or not encrypted or encrypted == canonical:
            raise ValueError
        return encrypted
    except Exception:
        raise AutenticError('CUSTODIA_DESCARGA_NO_COMPLETADA') from None


def leer_descarga_custodiada(cifrado, *, vinculo, descifrar):
    """Validate the decrypted snapshot against the independently supplied binding."""
    try:
        _vinculo_custodia(vinculo)
        plain = descifrar(cifrado)
        if type(plain) is not str or len(plain) > 8_100_000:
            raise ValueError
        envelope = json.loads(plain, object_pairs_hook=_config_unica)
        if (set(envelope) != set(vinculo) | {'version', 'pdfSha256', 'pdfBase64',
                'recuperadoEn', 'procedencia', 'firmaAcreditada'}
                or envelope['version'] != 'AUTENTIC_CUSTODIA_DESCARGA_LOCAL_V1'
                or any(envelope[k] != v for k, v in vinculo.items())
                or envelope['firmaAcreditada'] is not False
                or not _procedencia_descarga_integra(envelope['procedencia'], vinculo['ambiente'])
                or datetime.fromisoformat(envelope['recuperadoEn']).utcoffset() is None
                or type(envelope['pdfBase64']) is not str or len(envelope['pdfBase64']) > 8_000_000):
            raise ValueError
        evidence = EvidenciaPDF(envelope['pdfSha256'], base64.b64decode(envelope['pdfBase64'], validate=True))
        return evidence, {k: v for k, v in envelope.items() if k != 'pdfBase64'}
    except Exception:
        raise AutenticError('CUSTODIA_DESCARGA_NO_INTEGRA') from None


def verificar_zip(contenido, nombres_esperados):
    try:
        if (type(contenido) is not bytes or not 0 < len(contenido) <= MAX_ZIP
                or type(nombres_esperados) not in (tuple, list)
                or not 1 <= len(nombres_esperados) <= 5
                or any(type(n) is not str for n in nombres_esperados)
                or len(set(nombres_esperados)) != len(nombres_esperados)):
            raise ValueError
        with ZipFile(BytesIO(contenido)) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if (not 1 <= len(entries) <= 5 or len(set(names)) != len(names)
                    or set(names) != set(nombres_esperados)
                    or sum(e.file_size for e in entries) > MAX_ZIP):
                raise ValueError
            result = {}
            for entry in entries:
                if (entry.is_dir() or any(c in entry.filename for c in '/\\:\x00')
                        or entry.filename.startswith('.') or entry.flag_bits & 1
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > 6_000_000):
                    raise ValueError
                content = archive.read(entry)
                result[entry.filename] = EvidenciaPDF(validar_pdf(content), content)
            return result
    except (ValueError, BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError):
        raise AutenticError("ZIP_INVALIDO") from None
