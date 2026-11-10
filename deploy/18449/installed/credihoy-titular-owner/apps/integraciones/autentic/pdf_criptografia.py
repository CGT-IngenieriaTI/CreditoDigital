"""Offline PDF validation through pyHanko; never trust a root embedded in a PDF."""
from datetime import datetime,timezone
from copy import copy
from hashlib import sha256
from io import BytesIO
import logging
import re
import math
from contextlib import ExitStack
import json
import base64


def referencias_registro_sello(entorno):
    from pathlib import Path
    paths=tuple(entorno.get('CREDIHOY_AUTENTIC_SELLO_'+name+'_FILE','') for name in ('REGISTRO','AUTORIDAD'))
    if any(paths) and (not all(paths) or any(not Path(p).is_absolute() for p in paths)
            or Path(paths[0]).resolve()==Path(paths[1]).resolve()):
        raise ValueError('REFERENCIAS_SELLO_INDEPENDIENTES_REQUERIDAS')
    return paths


def validar_registro_sello(envelope, autoridad, *, ambiente, momento):
    """Independent signed trust/revocation snapshot; no trust on first PDF use."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        if (type(envelope) is not dict or set(envelope)!={'payload','firmaBase64'}
                or type(autoridad) is not bytes or len(autoridad)!=32):raise ValueError()
        payload=envelope['payload']
        if (type(payload) is not dict or set(payload)!={'version','proveedor','ambiente','referencia',
                'emitidoEn','vigenteHasta','certificados'} or payload['version']!='AUTENTIC_SELLO_REGISTRO_V1'
                or payload['proveedor']!='AUTENTIC' or payload['ambiente']!=ambiente
                or ambiente not in {'DEV','PRODUCCION'} or type(payload['referencia']) is not str
                or not 1<=len(payload['referencia'])<=500):raise ValueError()
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
        Ed25519PublicKey.from_public_bytes(autoridad).verify(base64.b64decode(envelope['firmaBase64'],validate=True),raw)
    except Exception:
        raise ValueError('REGISTRO_SELLO_FIRMA_O_CONTRATO_INVALIDO') from None
    try:
        issued,until=(datetime.fromisoformat(payload[k]) for k in ('emitidoEn','vigenteHasta'))
        if (any(v.utcoffset() is None for v in (issued,until,momento))
                or not issued<=momento<until or not 0<(until-issued).total_seconds()<=86400):raise ValueError()
    except (TypeError,ValueError):raise ValueError('REGISTRO_SELLO_VENCIDO_O_SIN_ZONA') from None
    records={}
    try:
        rows=payload['certificados']
        if type(rows) is not list or not 1<=len(rows)<=16:raise ValueError()
        for row in rows:
            if (type(row) is not dict or set(row)!={'certificadoSha256','spkiSha256','estado','vigenteDesde',
                    'vigenteHasta','revocadoEn','fuenteIndependiente'}
                    or row['estado'] not in {'ACTIVO','REVOCADO'}
                    or any(type(row[k]) is not str or not re.fullmatch('[a-f0-9]{64}',row[k])
                           for k in ('certificadoSha256','spkiSha256'))
                    or row['certificadoSha256'] in records or type(row['fuenteIndependiente']) is not str
                    or not 1<=len(row['fuenteIndependiente'])<=1000):raise ValueError()
            start,end=(datetime.fromisoformat(row[k]) for k in ('vigenteDesde','vigenteHasta'))
            if any(v.utcoffset() is None for v in (start,end)) or start>=end:raise ValueError()
            revoked=row['revocadoEn']
            if row['estado']=='REVOCADO':
                revoked=datetime.fromisoformat(revoked)
                if revoked.utcoffset() is None or revoked>momento:raise ValueError()
            elif revoked is not None:raise ValueError()
            records[row['certificadoSha256']]={**row,'desde':start,'hasta':end}
    except (ValueError,TypeError,KeyError):raise ValueError('REGISTRO_SELLO_CERTIFICADOS_INVALIDOS') from None
    return records,{'version':payload['version'],'registroSha256':sha256(raw).hexdigest(),
        'referenciaSha256':sha256(payload['referencia'].encode()).hexdigest(),
        'autoridadSha256':sha256(autoridad).hexdigest(),'emitidoEn':issued.isoformat(),'vigenteHasta':until.isoformat()}


def cargar_registro_sello(archivo, autoridad_archivo):
    """Read independently provisioned files, never provider URLs or PDF attachments."""
    from pathlib import Path
    import stat
    from .contratos import _config_unica
    def read(path,maximum):
        path=Path(path)
        if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):raise ValueError()
        with path.open('rb') as stream:
            import os
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=maximum
                    or os.name!='nt' and info.st_mode&0o022):raise ValueError()
            raw=stream.read(maximum+1)
            if len(raw)>maximum:raise ValueError()
            return raw
    try:
        if Path(archivo).resolve()==Path(autoridad_archivo).resolve():raise ValueError()
        envelope=json.loads(read(archivo,65536).decode('utf-8'),object_pairs_hook=_config_unica)
        authority=base64.b64decode(read(autoridad_archivo,128).strip(),validate=True)
        if len(authority)!=32:raise ValueError()
        return envelope,authority
    except (OSError,ValueError,TypeError,UnicodeError):raise ValueError('CUSTODIA_REGISTRO_SELLO_INVALIDA') from None


def _marca_autentic(source, target, original, firmado, recursos, paginas):
    """Recognise only the observed q/original/Q/header-mark transformation.

    An appended page is inspected as evidence, not silently accepted as another
    contractual page. Rendering is an additional check, never a text-only bypass.
    """
    result={'version':'AUTENTIC_MARCA_CONSTANCIA_OBSERVADA_V1','reconocida':False,
        'contenidoOriginalVisible':False,'anexoPermitido':False,'paginas':[],
        'codigo':'TRANSFORMACION_NO_RECONOCIDA'}
    if len(target.pages)!=len(source.pages)+1 or len(paginas)!=len(source.pages):return result
    expected=[b'q',b'BT',b'Tm',b'ET',b'BDC',b'BT',b'Tm',b'ET',b'q',b'rg',b're',b'f',b'g',
        b'Q',b'BT',b'Tm',b'Tf',b'rg',b'Tj',b'g',b'ET',b'EMC',b'BT',b'Tm',b'ET',b'Q']
    regions=[];document_id=None
    normalize=lambda value:' '.join(value.split())
    for index,page in enumerate(source.pages):
        other=target.pages[index];old=page.get_contents();new=other.get_contents()
        ops=old.operations if old is not None else []
        signed_ops=new.operations if new is not None else []
        if (len(signed_ops)!=len(ops)+28 or signed_ops[0]!=([],b'q')
                or signed_ops[1:len(ops)+1]!=ops or signed_ops[len(ops)+1]!=([],b'Q')):return result
        suffix=signed_ops[len(ops)+2:]
        if [op for _,op in suffix]!=expected:return result
        rect=[float(v) for v in suffix[10][0]]
        if len(rect)!=4:return result
        x,y,w,h=rect;box=[float(v) for v in page.mediabox]
        if (box[:2]!=[0,0] or not 0<=x<x+w<=box[2] or not box[3]-24<=y<y+h<=box[3]
                or not 0<w<=400 or not 0<h<=12):return result
        if (list(suffix[9][0])!=[1,1,1] or list(suffix[12][0])!=[0]
                or list(suffix[19][0])!=[0] or not 4<=float(suffix[16][0][1])<=8
                or not all(0<=float(v)<=0.5 for v in suffix[17][0])):return result
        # Every operand is constrained, including tag dictionaries and empty ops.
        mx,my=[float(v) for v in suffix[2][0][-2:]]
        matrices=[list(suffix[i][0]) for i in (2,6,15,23)]
        if (any(v!=[1,0,0,1,mx,my] for v in matrices[:3])
                or matrices[3]!=[1,0,0,1,x+w,my] or mx!=x or not y<=my<=y+h
                or list(suffix[4][0])!=['/Span',{'/MCID':0}]):return result
        empty={0,1,3,5,7,8,11,13,14,20,21,22,24,25}
        if any(suffix[i][0] for i in empty):return result
        if not all(paginas[index].get(k) is True for k in ('propiedadesIguales','anotacionesIguales','geometriaIgual')):return result
        left,right=recursos[index];font=str(suffix[16][0][0])
        if (set(left)!=set(right) or any(left[k]!=right[k] for k in left if k!='/Font')
                or font in left.get('/Font',{}) or set(right.get('/Font',{}))!=set(left.get('/Font',{}))|{font}
                or any(v!=right['/Font'].get(k) for k,v in left.get('/Font',{}).items())):return result
        old_text=normalize(page.extract_text() or '');new_text=normalize(other.extract_text() or '')
        tail=new_text[len(old_text):].strip() if new_text.startswith(old_text) else ''
        match=re.fullmatch(r'Firmado electr[o\u00f3]nicamente\. Documento No\. ([0-9a-f-]{36})',tail)
        if not match:return result
        from uuid import UUID
        reference=str(UUID(match[1]))
        if document_id is not None and reference!=document_id:return result
        document_id=reference;regions.append(rect)
    result.update(reconocida=True,operadoresOriginalesConservados=True,recursosOriginalesConservados=True)
    annex=normalize(target.pages[-1].extract_text() or '')
    result['anexo']={'paginasAgregadas':1,'referenciaCoincide':document_id in annex,
        'textoSha256':sha256(annex.encode()).hexdigest(),
        'numeroPaginasOriginalesCoincide':bool(re.search(r'\b'+str(len(source.pages))+r' P[\u00e1a]ginas$',annex)),
        'esAceptacionPersonal':False,'vinculoProveedorVerificable':False}
    try:
        import pypdfium2 as pdfium
        from importlib.metadata import version
        if version('pypdfium2')!='5.14.0':raise ValueError('RENDER_VERSION_NO_ACREDITADA')
        result['renderer']='pypdfium2:5.14.0'
        with pdfium.PdfDocument(original) as old_doc,pdfium.PdfDocument(firmado) as new_doc:
            for index,rect in enumerate(regions):
                checks=[]
                for scale in (2,3):
                    with ExitStack() as pages_stack:
                        a,b=old_doc[index],new_doc[index]
                        pages_stack.callback(a.close);pages_stack.callback(b.close)
                        if max(a.get_size())*scale>4096:raise ValueError('RENDER_LIMITE')
                        with ExitStack() as bitmap_stack:
                            ab,bb=a.render(scale=scale),b.render(scale=scale)
                            bitmap_stack.callback(ab.close);bitmap_stack.callback(bb.close)
                            if (ab.width,ab.height,ab.n_channels,ab.stride)!=(bb.width,bb.height,bb.n_channels,bb.stride):
                                raise ValueError('RENDER_GEOMETRIA_CAMBIO')
                            if ab.n_channels not in (3,4):raise ValueError('RENDER_FORMATO_NO_ADMITIDO')
                            x,y,w,h=rect;channels=ab.n_channels
                            region=(max(0,math.floor(x*scale)),max(0,math.floor((a.get_height()-y-h)*scale)),
                                min(ab.width,math.ceil((x+w)*scale)),min(ab.height,math.ceil((a.get_height()-y)*scale)))
                            old_bytes,new_bytes=bytes(ab.buffer),bytes(bb.buffer)
                            left,right=region[0]*channels,region[2]*channels
                            old_digest,new_digest=sha256(),sha256();blank=True
                            for row in range(ab.height):
                                start=row*ab.stride;end=start+ab.width*channels
                                if region[1]<=row<region[3]:
                                    blank=blank and all(v==255 for v in old_bytes[start+left:start+right])
                                    for digest,data in ((old_digest,old_bytes),(new_digest,new_bytes)):
                                        digest.update(data[start:start+left]);digest.update(data[start+right:end])
                                else:
                                    old_digest.update(old_bytes[start:end]);new_digest.update(new_bytes[start:end])
                            checks.append({'escala':scale,'fueraDeMarcaIdentico':old_digest.digest()==new_digest.digest(),
                                'areaOriginalSinGrafica':blank,'originalFueraMarcaSha256':old_digest.hexdigest(),
                                'firmadoFueraMarcaSha256':new_digest.hexdigest(),'regionPixeles':region})
                result['paginas'].append({'pagina':index+1,'render':checks})
        result['contenidoOriginalVisible']=all(c['fueraDeMarcaIdentico'] and c['areaOriginalSinGrafica']
            for page in result['paginas'] for c in page['render'])
        result['codigo']=('ANEXO_REQUIERE_VINCULO_VERIFICABLE' if result['contenidoOriginalVisible'] else
            'AUTENTIC_MARCA_OCULTA_O_CAMBIA_GRAFICA_ORIGINAL')
    except Exception as error:
        result['codigo']='RENDER_TRANSFORMACION_NO_ACREDITADO';result['tipo']=type(error).__name__
    return result


def comparar_contenido_pdf(original, firmado):
    """Compare rendering inputs, not text alone or PDF serialization hashes.

    Provider overlays and appended evidence pages remain explicit differences.
    A rewritten PDF with identical page content/resources can be accredited;
    unknown additions never become an automatic equivalence exception.
    """
    result = {'version': 'PDF_CONTENIDO_V1', 'correspondenciaOriginal': False,
        'originalSha256': sha256(original).hexdigest(), 'firmadoSha256': sha256(firmado).hexdigest(),
        'codigo': 'CONTENIDO_PDF_NO_ACREDITADO', 'paginas': []}
    try:
        from pypdf import PdfReader
        from pypdf.generic import ByteStringObject, TextStringObject
        from .contratos import validar_pdf
        validar_pdf(original); validar_pdf(firmado)
        source = PdfReader(BytesIO(original), strict=True)
        target = PdfReader(BytesIO(firmado), strict=True)
        if max(len(source.pages), len(target.pages)) > 40:
            raise ValueError('LIMITE_PAGINAS')
        result.update(paginasOriginal=len(source.pages), paginasFirmado=len(target.pages),
            originalBinarioConservado=firmado.startswith(original))
        budget = [100000]

        def canonical(value, ancestors=(), depth=0):
            budget[0] -= 1
            if depth > 40 or budget[0] < 0:
                raise ValueError('LIMITE_ESTRUCTURA')
            obj = value.get_object() if hasattr(value, 'get_object') else value
            if isinstance(obj, (dict, list, tuple)):
                if id(obj) in ancestors:
                    raise ValueError('ESTRUCTURA_CICLICA')
                ancestors = (*ancestors, id(obj))
            if isinstance(obj, dict):
                fields = {str(k): canonical(v, ancestors, depth + 1) for k, v in obj.items()
                    if not (hasattr(obj, 'get_data') and str(k) in {'/Length', '/Filter', '/DecodeParms'})}
                if hasattr(obj, 'get_data'):
                    data = obj.get_data()
                    if len(data) > 20_000_000:
                        raise ValueError('LIMITE_STREAM')
                    return ('stream', sha256(data).hexdigest(), fields)
                return fields
            if isinstance(obj, (list, tuple)):
                return [canonical(v, ancestors, depth + 1) for v in obj]
            if isinstance(obj, (bytes, ByteStringObject)):
                return ('bytes', bytes(obj).hex())
            if isinstance(obj, TextStringObject):
                return ('text', str(obj))
            return (type(obj).__name__, str(obj))

        def annotations(page):
            rows = []
            for ref in page.get('/Annots', []):
                obj = ref.get_object()
                # Invisible signature widgets cannot cover original page content.
                if obj.get('/FT') == '/Sig' and list(obj.get('/Rect', [])) == [0, 0, 0, 0]:
                    continue
                rows.append(canonical({k: v for k, v in obj.items() if str(k) != '/P'}))
            return rows

        for reader in (source, target):
            if any(key in reader.trailer['/Root'] for key in ('/OpenAction', '/AA', '/Collection')):
                raise ValueError('CONTENIDO_ACTIVO')
            names = reader.trailer['/Root'].get('/Names', {})
            names = names.get_object() if hasattr(names, 'get_object') else names
            if any(key in names for key in ('/JavaScript', '/EmbeddedFiles')):
                raise ValueError('CONTENIDO_ACTIVO')
            form = reader.trailer['/Root'].get('/AcroForm', {})
            form = form.get_object() if hasattr(form, 'get_object') else form
            if '/XFA' in form:
                raise ValueError('CONTENIDO_ACTIVO')
        def catalog_graphics(reader):
            root = reader.trailer['/Root']
            form = root.get('/AcroForm', {})
            form = form.get_object() if hasattr(form, 'get_object') else form
            return canonical({
                'catalog': {k: root[k] for k in ('/OCProperties', '/OutputIntents') if k in root},
                'form': {k: form[k] for k in ('/DA', '/DR', '/Q', '/NeedAppearances') if k in form}})
        catalog_equal = catalog_graphics(source) == catalog_graphics(target)
        result['configuracionGraficaCatalogoIgual'] = catalog_equal
        recursos=[]
        for number, page in enumerate(source.pages):
            if number >= len(target.pages):
                result['paginas'].append({'pagina': number + 1, 'conservada': False})
                continue
            other = target.pages[number]
            left, right = page.get_contents(), other.get_contents()
            operations = (canonical(left.operations if left is not None else []) ==
                canonical(right.operations if right is not None else []))
            left_resources=canonical(page.get('/Resources', {}));right_resources=canonical(other.get('/Resources', {}))
            recursos.append((left_resources,right_resources))
            resources = left_resources==right_resources
            excluded = {'/Parent', '/Contents', '/Resources', '/Annots', '/StructParents'}
            properties = canonical({k: v for k, v in page.items() if str(k) not in excluded}) == canonical(
                {k: v for k, v in other.items() if str(k) not in excluded})
            geometry = (list(page.mediabox) == list(other.mediabox) and
                list(page.cropbox) == list(other.cropbox) and page.rotation == other.rotation)
            annots = annotations(page) == annotations(other)
            result['paginas'].append({'pagina': number + 1, 'operadoresIguales': operations,
                'recursosIguales': resources, 'propiedadesIguales': properties,
                'anotacionesIguales': annots, 'geometriaIgual': geometry,
                'conservada': operations and resources and properties and annots and geometry})
        result['correspondenciaOriginal'] = (catalog_equal and len(source.pages) == len(target.pages) and
            all(row['conservada'] for row in result['paginas']))
        result['codigo'] = ('CONTENIDO_PDF_EQUIVALENTE' if result['correspondenciaOriginal'] else
            'CAMBIOS_DOCUMENTALES_PROVEEDOR_REQUIEREN_VERIFICACION')
        if not result['correspondenciaOriginal']:
            result['transformacionAutentic']=_marca_autentic(source,target,original,firmado,recursos,result['paginas'])
    except Exception as error:
        result['tipo'] = type(error).__name__
        if str(error) in {'LIMITE_PAGINAS', 'LIMITE_ESTRUCTURA', 'ESTRUCTURA_CICLICA',
                'LIMITE_STREAM', 'CONTENIDO_ACTIVO'}:
            result['motivo'] = str(error)
    return result


def verificar_sello_proveedor(original, firmado, *, raices=(), crls=(), ocsps=(), momento=None,
        registro_sello=None, autoridad_sello=None, ambiente='DEV'):
    """Provider seal and content only; personal acceptance is verified separately."""
    result = {'contenido': comparar_contenido_pdf(original, firmado), 'firmaIntegra': False,
        'cadenaConfiable': False, 'fechaConfiableTSA': False, 'certificadosSha256': [],
        'codigo': 'SELLO_PROVEEDOR_NO_ACREDITADO'}
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if (isinstance(logger, logging.Logger) and name.startswith(('pyhanko', 'pyhanko_certvalidator'))
                and any(not isinstance(v, logging.NullHandler) for v in logger.handlers)):
            result['codigo'] = 'LOG_CRIPTOGRAFICO_NO_AISLADO'
            return result
    for namespace in ('pyhanko', 'pyhanko_certvalidator'):
        logger = logging.getLogger(namespace)
        if not any(isinstance(v, logging.NullHandler) for v in logger.handlers):
            logger.addHandler(logging.NullHandler())
        logger.propagate = False
    try:
        from asn1crypto import x509, crl, ocsp, pem
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature, SignatureCoverageLevel
        from pyhanko_certvalidator import ValidationContext
        when = momento or datetime.now(timezone.utc)
        if when.utcoffset() is None:
            raise ValueError('FECHA_SIN_ZONA')
        if not raices and registro_sello is None and autoridad_sello is None:
            result['codigo']='RAICES_DE_CONFIANZA_NO_CONFIGURADAS'
            return result
        pdf = PdfFileReader(BytesIO(firmado), strict=True)
        signatures = pdf.embedded_regular_signatures
        if not signatures:
            result['codigo'] = 'PDF_SIN_FIRMA_CRIPTOGRAFICA'
            return result
        if (registro_sello is None and autoridad_sello is None and
                any(sig.signer_cert.subject==sig.signer_cert.issuer for sig in signatures)):
            result['codigo']='REGISTRO_REVOCACION_SELLO_REQUERIDO'
            return result
        if registro_sello is not None or autoridad_sello is not None:
            from cryptography import x509 as cert_crypto
            from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
            records,proof=validar_registro_sello(registro_sello,autoridad_sello,ambiente=ambiente,momento=when)
            result['registroConfianza']=proof
            pinned=[]
            for sig in signatures:
                raw=sig.signer_cert.dump();fingerprint=sha256(raw).hexdigest()
                record=records.get(fingerprint)
                if record is None:raise ValueError('SELLO_NO_REGISTRADO')
                if record['estado']=='REVOCADO':raise ValueError('SELLO_REVOCADO')
                if not record['desde']<=when<record['hasta']:raise ValueError('SELLO_FUERA_VIGENCIA_REGISTRO')
                cert=cert_crypto.load_der_x509_certificate(raw)
                spki=cert.public_key().public_bytes(Encoding.DER,PublicFormat.SubjectPublicKeyInfo)
                if sha256(spki).hexdigest()!=record['spkiSha256']:raise ValueError('SELLO_CLAVE_NO_CORRESPONDE')
                if not raices:
                    # A self-issued seal becomes a root only after the independent
                    # authority pins both its certificate and public key.
                    cert.verify_directly_issued_by(cert)
                    pinned.append(raw)
            if not raices:raices=tuple(pinned)
            result['revocacion']='NO_REVOCADO_SEGUN_REGISTRO_INSTITUCIONAL_VIGENTE'
        if not raices:
            result['codigo'] = 'RAICES_DE_CONFIANZA_NO_CONFIGURADAS'
            return result
        # Never fetch AIA/CRL/OCSP or trust a certificate supplied by the PDF itself.
        der = lambda value: pem.unarmor(value)[2] if pem.detect(value) else value
        context = ValidationContext(trust_roots=[x509.Certificate.load(der(v)) for v in raices],
            crls=[crl.CertificateList.load(der(v)) for v in crls],
            ocsps=[ocsp.OCSPResponse.load(der(v)) for v in ocsps],
            allow_fetching=False, revocation_mode='require', moment=when)
        statuses = [validate_pdf_signature(sig, signer_validation_context=context,
            ts_validation_context=context) for sig in signatures]
        result['firmaIntegra'] = all(s.intact and s.valid and s.docmdp_ok and
            s.coverage >= SignatureCoverageLevel.ENTIRE_REVISION for s in statuses)
        result['cadenaConfiable'] = all(s.trusted for s in statuses)
        result['certificadosSha256'] = [sha256(s.signing_cert.dump()).hexdigest() for s in statuses]
        result['fechaConfiableTSA'] = all(s.timestamp_validity is not None and
            s.timestamp_validity.intact and s.timestamp_validity.valid and
            s.timestamp_validity.trusted for s in statuses)
        result['codigo'] = 'SELLO_PROVEEDOR_ANALIZADO'
    except Exception as error:
        result['tipo'] = type(error).__name__
        if str(error) in {'REGISTRO_SELLO_FIRMA_O_CONTRATO_INVALIDO','REGISTRO_SELLO_VENCIDO_O_SIN_ZONA',
                'REGISTRO_SELLO_CERTIFICADOS_INVALIDOS','SELLO_NO_REGISTRADO','SELLO_REVOCADO',
                'SELLO_FUERA_VIGENCIA_REGISTRO','SELLO_CLAVE_NO_CORRESPONDE'}:result['codigo']=str(error)
    return result


def verificar_pdf(original,firmado,documento_identidad,*,raices=(),crls=(),ocsps=(),momento=None,
        politicas_titular=()):
    result={'correspondenciaOriginal':False,'firmaIntegra':False,'cadenaConfiable':False,
        'identidadCertificado':False,'fechaConfiable':False,'certificadosSha256':[],
        'rolCertificado':'NO_DETERMINADO','participacionElectronicaAcreditada':False,
        'codigo':'VERIFICADOR_PDF_NO_DISPONIBLE'}
    try:
        from asn1crypto import x509,crl,ocsp,pem
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature,SignatureCoverageLevel
        from pyhanko.sign.diff_analysis import DEFAULT_DIFF_POLICY,ModificationLevel,DiffResult
        from pyhanko_certvalidator import ValidationContext
    except ImportError:return result
    # Validation diagnostics can contain certificate subjects; not application logs.
    for name,logger in list(logging.root.manager.loggerDict.items()):
        if isinstance(logger,logging.Logger) and name.startswith(('pyhanko.','pyhanko_certvalidator.')):
            if any(not isinstance(v,logging.NullHandler) for v in logger.handlers):
                result['codigo']='LOG_CRIPTOGRAFICO_NO_AISLADO';return result
    for namespace in ('pyhanko','pyhanko_certvalidator'):
        logger=logging.getLogger(namespace)
        if any(not isinstance(v,logging.NullHandler) for v in logger.handlers):
            result['codigo']='LOG_CRIPTOGRAFICO_NO_AISLADO';return result
        if not any(isinstance(v,logging.NullHandler) for v in logger.handlers):logger.addHandler(logging.NullHandler())
        logger.propagate=False
    when=momento or datetime.now(timezone.utc)
    phase='LECTURA_PDF'
    try:
        if when.utcoffset() is None:raise ValueError
        source=PdfFileReader(BytesIO(original),strict=True)
        target=PdfFileReader(BytesIO(firmado),strict=True)
        signatures=target.embedded_regular_signatures
        if not signatures:
            result['codigo']='PDF_SIN_FIRMA_CRIPTOGRAFICA';return result
        if not firmado.startswith(original) or firmado==original:
            result['codigo']='REESCRITURA_PDF_REQUIERE_CORRESPONDENCIA_PROVEEDOR';return result
        phase='DIFERENCIAS_INCREMENTALES'
        # Signing may add append-only SigFlags. Never ignore an arbitrary downgrade.
        old_flags=int(source.root['/AcroForm'].get('/SigFlags',0))
        new_flags=int(target.root['/AcroForm'].get('/SigFlags',0))
        if old_flags not in (0,1,3) or new_flags!=3:
            result['codigo']='SIGFLAGS_NO_CONSERVA_PROTECCION';return result
        policy=copy(DEFAULT_DIFF_POLICY);policy.form_rule=copy(policy.form_rule)
        policy.form_rule.ignored_acroform_keys=policy.form_rule.ignored_acroform_keys|{'/SigFlags'}
        diff=policy.review_file(target,source.total_revisions-1)
        signature_fields={sig.field_name for sig in signatures}
        result['correspondenciaOriginal']=(isinstance(diff,DiffResult)
            and diff.modification_level<=ModificationLevel.FORM_FILLING
            and diff.changed_form_fields<=signature_fields)
        if not result['correspondenciaOriginal']:
            result['codigo']='PDF_ORIGINAL_MODIFICADO';return result
        phase='CONTEXTO_CONFIANZA'
        der=lambda raw:pem.unarmor(raw)[2] if pem.detect(raw) else raw
        context=ValidationContext(trust_roots=[x509.Certificate.load(der(v)) for v in raices],
            crls=[crl.CertificateList.load(der(v)) for v in crls],
            ocsps=[ocsp.OCSPResponse.load(der(v)) for v in ocsps],
            allow_fetching=False,revocation_mode='require',moment=when)
        phase='VALIDACION_FIRMAS'
        statuses=[validate_pdf_signature(sig,signer_validation_context=context,
            ts_validation_context=context,skip_diff=False) for sig in signatures]
        result['firmaIntegra']=all(s.intact and s.valid and s.coverage>=SignatureCoverageLevel.ENTIRE_REVISION
            and s.docmdp_ok for s in statuses)
        result['cadenaConfiable']=all(s.trusted for s in statuses)
        result['certificadosSha256']=[sha256(s.signing_cert.dump()).hexdigest() for s in statuses]
        identity_matches=[s for s in statuses if s.signing_cert.subject.native.get('serial_number')==documento_identidad]
        result['identidadCertificado']=bool(identity_matches) and all(s.trusted and s.intact and s.valid for s in identity_matches)
        # A provider/sealing certificate can have a valid TSA without naming the holder.
        result['fechaConfiable']=bool(statuses) and all(s.timestamp_validity is not None
            and s.timestamp_validity.intact and s.timestamp_validity.valid and s.timestamp_validity.trusted
            for s in statuses)
        trusted_roots={sha256(der(v)).hexdigest() for v in raices}
        authorized=[]
        for item in politicas_titular:
            if (type(item) is not dict or set(item)!={'raizSha256','politicaOid','referencia'}
                    or item['raizSha256'] not in trusted_roots
                    or type(item['referencia']) is not str or not item['referencia'].strip()):
                raise ValueError('POLITICA_CERTIFICADO_PERSONAL_INVALIDA')
            from asn1crypto.core import ObjectIdentifier
            ObjectIdentifier(item['politicaOid']).dump()
            authorized.append(item)
        for status in identity_matches:
            cert=status.signing_cert
            usages=cert.key_usage_value.native if cert.key_usage_value is not None else set()
            policies={v['policy_identifier'].dotted for v in cert.certificate_policies_value or ()}
            chain={sha256(v.dump()).hexdigest() for v in status.validation_path or ()}
            if (not cert.ca and {'digital_signature','non_repudiation'}<=usages
                    and any(v['raizSha256'] in chain and v['politicaOid'] in policies for v in authorized)):
                result['rolCertificado']='FIRMANTE_TITULAR'
                result['participacionElectronicaAcreditada']=bool(status.trusted and status.intact and status.valid)
        if result['fechaConfiable']:
            result['firmadoEn']=max(s.timestamp_validity.timestamp for s in statuses).isoformat()
        result['codigo']='PDF_CRIPTOGRAFIA_ANALIZADA' if raices else 'RAICES_DE_CONFIANZA_NO_CONFIGURADAS'
    except Exception as error:
        result['codigo']='PDF_CRIPTOGRAFIA_NO_ACREDITADA'
        result['fase']=phase;result['tipo']=type(error).__name__
    return result
