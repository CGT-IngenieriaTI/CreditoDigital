"""Offline PDF validation through pyHanko; never trust a root embedded in a PDF."""
from datetime import datetime,timezone
from copy import copy
from hashlib import sha256
from io import BytesIO
import logging


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
