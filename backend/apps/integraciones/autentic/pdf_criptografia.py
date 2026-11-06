"""Offline PDF validation through pyHanko; never trust a root embedded in a PDF."""
from datetime import datetime,timezone
from copy import copy
from hashlib import sha256
from io import BytesIO
import logging


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
        for number, page in enumerate(source.pages):
            if number >= len(target.pages):
                result['paginas'].append({'pagina': number + 1, 'conservada': False})
                continue
            other = target.pages[number]
            left, right = page.get_contents(), other.get_contents()
            operations = (canonical(left.operations if left is not None else []) ==
                canonical(right.operations if right is not None else []))
            resources = canonical(page.get('/Resources', {})) == canonical(other.get('/Resources', {}))
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
    except Exception as error:
        result['tipo'] = type(error).__name__
        if str(error) in {'LIMITE_PAGINAS', 'LIMITE_ESTRUCTURA', 'ESTRUCTURA_CICLICA',
                'LIMITE_STREAM', 'CONTENIDO_ACTIVO'}:
            result['motivo'] = str(error)
    return result


def verificar_sello_proveedor(original, firmado, *, raices=(), crls=(), ocsps=(), momento=None):
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
        if not raices:
            result['codigo'] = 'RAICES_DE_CONFIANZA_NO_CONFIGURADAS'
            return result
        # Never fetch AIA/CRL/OCSP or trust a certificate supplied by the PDF itself.
        der = lambda value: pem.unarmor(value)[2] if pem.detect(value) else value
        context = ValidationContext(trust_roots=[x509.Certificate.load(der(v)) for v in raices],
            crls=[crl.CertificateList.load(der(v)) for v in crls],
            ocsps=[ocsp.OCSPResponse.load(der(v)) for v in ocsps],
            allow_fetching=False, revocation_mode='require', moment=when)
        pdf = PdfFileReader(BytesIO(firmado), strict=True)
        signatures = pdf.embedded_regular_signatures
        if not signatures:
            result['codigo'] = 'PDF_SIN_FIRMA_CRIPTOGRAFICA'
            return result
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
