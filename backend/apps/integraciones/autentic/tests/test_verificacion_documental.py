"""Offline synthetic PDFs/certificates; never provider evidence or production trust."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from io import BytesIO
import json
import socket
import unittest
from unittest.mock import patch
from uuid import uuid4
from pypdf import PdfWriter
from ..contratos import AutenticError,preparar_certificacion
from ..evidencia import analizar_firma_documental,verificar_firma_documental
from ..pdf_criptografia import verificar_pdf


def pdf():
    writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    out=BytesIO();writer.write(out);return out.getvalue()


def context(original,signed,when):
    case,send,document=map(str,(uuid4(),uuid4(),uuid4()))
    result={'solicitudId':case,'envioId':send,'documentoOperacionId':document,
        'estado':'FIRMA_REPORTADA_NO_VERIFICADA','preparadoEn':(when-timedelta(minutes=2)).isoformat(),
        'vinculo':{'solicitudId':case,'documentoOperacionId':document,'pdfSha256':sha256(original).hexdigest(),
            'firmanteReferencia':sha256(b'SYNTHETIC-ID').hexdigest()},
        'recibo':{'transaccion':'SYNTHETIC-TX','codigo_operacion':1000,'estado':'FIRMA_REPORTADA_NO_VERIFICADA',
            'tipo_respuesta':'reply_signature','fecha_proveedor':(when-timedelta(minutes=1)).isoformat(),
            'url_documentos':'https://synthetic.example.invalid/zip'},
        'descarga':{'solicitudId':case,'envioId':send,'originalSha256':sha256(original).hexdigest(),
            'pdfSha256':sha256(signed).hexdigest(),'transaccionReferencia':sha256(b'SYNTHETIC-TX').hexdigest(),
            'recuperadoEn':when.isoformat(),'procedencia':{'version':'AUTENTIC_DESCARGA_V1','ambiente':'DEV',
                'http':200,'TLSVerificado':True,'sinRedirecciones':True,'zipSha256':'a'*64,
                'urlSha256':sha256(b'https://synthetic.example.invalid/zip').hexdigest()}}}
    result['recibo']['respuesta_original']={'transactionId':'SYNTHETIC-TX','operationCode':1000,
        'processedDate':result['recibo']['fecha_proveedor'],'type':'reply_signature',
        'urlDocuments':result['recibo']['url_documentos']}
    return result


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc);self.original=pdf();self.signed=self.original
        self.context=context(self.original,self.signed,self.now)
        self.crypto=patch('apps.integraciones.autentic.pdf_criptografia.verificar_pdf',return_value={
            'correspondenciaOriginal':False,'firmaIntegra':False,'cadenaConfiable':False,
            'identidadCertificado':False,'fechaConfiable':False,'codigo':'SYNTHETIC_UNSIGNED'})
        self.crypto.start();self.addCleanup(self.crypto.stop)

    def analyze(self,**kwargs):
        return analizar_firma_documental(original=self.original,firmado=self.signed,transaccion='SYNTHETIC-TX',
            documento_identidad='SYNTHETIC-ID',contexto=kwargs.pop('contexto',self.context),momento=self.now,**kwargs)

    def test_technical_integrity_receipt_and_ownership_do_not_attest_signing(self):
        result=self.analyze()
        for name in ('integridadPDF','correlacionExpediente','reciboProveedor','procedenciaDescarga','fechaProveedorConZona'):
            self.assertTrue(result[name],name)
        self.assertFalse(result['firmaAcreditada']);self.assertFalse(result['firmaCriptografica'])
        self.assertEqual(result['estado'],'PENDIENTE_VERIFICACION')
        self.assertNotIn('SYNTHETIC-ID',json.dumps(result))

    def test_foreign_case_operation_original_signed_hash_identity_or_transaction_are_invalid(self):
        for area,key in (('vinculo','solicitudId'),('vinculo','documentoOperacionId'),('vinculo','pdfSha256'),
                ('vinculo','firmanteReferencia'),('descarga','envioId'),('descarga','pdfSha256'),
                ('descarga','transaccionReferencia')):
            candidate=deepcopy(self.context);candidate[area][key]='FOREIGN'
            self.assertEqual(self.analyze(contexto=candidate)['estado'],'INVALIDA')

    def test_wrong_receipt_environment_or_unsafe_provenance_never_promotes_signature(self):
        for area,key,value in (('recibo','codigo_operacion',2000),('recibo','transaccion','OTHER'),
                ('recibo','tipo_respuesta','reply'),('descarga','procedencia',{})):
            candidate=deepcopy(self.context);candidate[area][key]=value
            result=self.analyze(contexto=candidate)
            self.assertFalse(result['firmaAcreditada'])
            self.assertFalse(result['reciboProveedor'] if area=='recibo' else result['procedenciaDescarga'])
        candidate=deepcopy(self.context);candidate['descarga']['procedencia']['ambiente']='PROD'
        self.assertFalse(self.analyze(contexto=candidate)['procedenciaDescarga'])

    def test_provider_date_is_not_assumed_utc_and_must_be_within_claim_and_download(self):
        for value in ('2026-10-29 10:00:00',(self.now+timedelta(days=1)).isoformat(),
                (self.now-timedelta(days=1)).isoformat()):
            candidate=deepcopy(self.context);candidate['recibo']['fecha_proveedor']=value
            self.assertFalse(self.analyze(contexto=candidate)['fechaProveedorConZona'])

    def test_document_number_is_not_inferred_or_treated_as_authentic_by_presence(self):
        self.assertEqual(self.analyze()['certificacionPosterior'],'PENDIENTE_DOCUMENT_NUMBER_AUTENTICO')
        for number in ('SYNTHETIC-ID','prueba.pdf','SYNTHETIC-TX'):
            result=self.analyze(document_number=number)
            self.assertEqual(result['certificacionPosterior'],'IDENTIFICADOR_RECIBIDO_NO_VERIFICADO')
            self.assertFalse(result['firmaAcreditada'])
        request=preparar_certificacion('SYNTHETIC-TX','PROVIDER-IDENTIFIER')
        self.assertEqual(request.payload['documentNumber'],'PROVIDER-IDENTIFIER')
        with self.assertRaises(AutenticError):preparar_certificacion('SYNTHETIC-TX','')

    def test_legacy_verifier_keeps_closed_and_exposes_partial_result_without_private_data(self):
        with self.assertRaises(AutenticError) as error:
            verificar_firma_documental(original=self.original,firmado=self.signed,transaccion='SYNTHETIC-TX',
                document_number=None,documento_identidad='SYNTHETIC-ID',contexto=self.context,momento=self.now)
        self.assertFalse(error.exception.resultado['firmaAcreditada'])
        self.assertNotIn('SYNTHETIC-ID',str(error.exception))

    def test_missing_crypto_dependency_is_explicit_and_never_validates_a_signature(self):
        import builtins
        native=builtins.__import__
        def missing(name,*args,**kwargs):
            if name=='asn1crypto':raise ImportError('SYNTHETIC_MISSING_DEPENDENCY')
            return native(name,*args,**kwargs)
        with patch('builtins.__import__',missing):
            result=verificar_pdf(self.original,self.signed,'SYNTHETIC-ID')
        self.assertEqual(result['codigo'],'VERIFICADOR_PDF_NO_DISPONIBLE')
        self.assertFalse(result['firmaIntegra'])

    def test_certificate_subject_match_or_provider_seal_does_not_establish_personal_participation(self):
        for matches in (True,False):
            crypto={'correspondenciaOriginal':True,'firmaIntegra':True,'cadenaConfiable':True,
                'identidadCertificado':matches,'fechaConfiable':True,'rolCertificado':'NO_DETERMINADO'}
            with patch('apps.integraciones.autentic.pdf_criptografia.verificar_pdf',return_value=crypto):
                result=self.analyze()
            self.assertTrue(result['firmaCriptografica'])
            self.assertEqual(result['coincidenciaTitularCertificado'],matches)
            self.assertFalse(result['identidadFirmante']);self.assertFalse(result['participacionFirmante'])
            self.assertEqual(result['estado'],'PENDIENTE_VERIFICACION')

    def test_certification_identifier_is_separate_from_identity_and_participation(self):
        for number in (None,'SYNTHETIC-PROVIDER-NUMBER'):
            result=self.analyze(document_number=number)
            self.assertIn('EVIDENCIA_IDENTIDAD_Y_PARTICIPACION_PROVEEDOR_PENDIENTE',result['pendientes'])
            self.assertNotIn('CORRELACION_DOCUMENT_NUMBER_Y_EVIDENCIA_PROVEEDOR_PENDIENTE',result['pendientes'])
            self.assertFalse(result['firmaAcreditada'])


class CryptographyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from asn1crypto import x509 as asn1x509,keys
        from pyhanko.sign import signers
        from pyhanko.sign.fields import append_signature_field,SigFieldSpec,ensure_sig_flags
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko_certvalidator.registry import SimpleCertificateStore
        cls.now=datetime.now(timezone.utc)
        initial=IncrementalPdfFileWriter(BytesIO(pdf()))
        append_signature_field(initial,SigFieldSpec(sig_field_name='SyntheticSignature'))
        ensure_sig_flags(initial,lock_sig_flags=False)
        initial_bytes=BytesIO();initial.write(initial_bytes);cls.original=initial_bytes.getvalue()
        root_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        leaf_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        root_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'SYNTHETIC TEST ROOT ONLY')])
        leaf_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'SYNTHETIC TEST SIGNER ONLY'),
            x509.NameAttribute(NameOID.SERIAL_NUMBER,'SYNTHETIC-ID')])
        def certificate(subject,issuer,key,issuer_key,ca):
            return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(cls.now-timedelta(days=1))
                .not_valid_after(cls.now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=ca,path_length=None),True)
                .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=not ca,key_encipherment=False,
                    data_encipherment=False,key_agreement=False,key_cert_sign=ca,crl_sign=ca,encipher_only=False,decipher_only=False),True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),False)
                .add_extension(x509.CertificatePolicies([x509.PolicyInformation(
                    x509.ObjectIdentifier('1.3.6.1.4.1.55555.9'),None)]),False)
                .sign(issuer_key,hashes.SHA256()))
        root=certificate(root_name,root_name,root_key,root_key,True)
        leaf=certificate(leaf_name,root_name,leaf_key,root_key,False)
        crl=(x509.CertificateRevocationListBuilder().issuer_name(root_name).last_update(cls.now-timedelta(hours=1))
            .next_update(cls.now+timedelta(days=1)).add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),False).sign(root_key,hashes.SHA256()))
        cls.roots=(root.public_bytes(serialization.Encoding.DER),)
        cls.crls=(crl.public_bytes(serialization.Encoding.DER),)
        revoked=(x509.RevokedCertificateBuilder().serial_number(leaf.serial_number)
            .revocation_date(cls.now-timedelta(hours=1)).build())
        cls.revoked_crls=((x509.CertificateRevocationListBuilder().issuer_name(root_name)
            .last_update(cls.now-timedelta(hours=1)).next_update(cls.now+timedelta(days=1))
            .add_revoked_certificate(revoked).add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),False)
            .sign(root_key,hashes.SHA256())).public_bytes(serialization.Encoding.DER),)
        store=SimpleCertificateStore();store.register(asn1x509.Certificate.load(cls.roots[0]))
        signer=signers.SimpleSigner(signing_cert=asn1x509.Certificate.load(leaf.public_bytes(serialization.Encoding.DER)),
            signing_key=keys.PrivateKeyInfo.load(leaf_key.private_bytes(serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,serialization.NoEncryption())),cert_registry=store)
        cls.signer=signer
        cls.root_key=root_key;cls.root_name=root_name
        cls.signed=signers.sign_pdf(IncrementalPdfFileWriter(BytesIO(cls.original)),
            signature_meta=signers.PdfSignatureMetadata(field_name='SyntheticSignature'),signer=signer).getvalue()

    def verify(self,**kwargs):
        native=socket.socket.connect
        def only_event_loop_loopback(sock,address):
            if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):
                return native(sock,address)
            raise AssertionError('NO_EXTERNAL_NETWORK')
        # Windows asyncio uses a local socket pair even with fetching disabled.
        with patch.object(socket.socket,'connect',only_event_loop_loopback):
            return verificar_pdf(kwargs.pop('original',self.original),kwargs.pop('firmado',self.signed),
                kwargs.pop('documento_identidad','SYNTHETIC-ID'),raices=kwargs.pop('raices',self.roots),
                crls=kwargs.pop('crls',self.crls),momento=self.now,**kwargs)

    def test_real_crypto_on_synthetic_chain_and_crl_without_inventing_trusted_time(self):
        report=self.verify()
        self.assertTrue(report['correspondenciaOriginal'],report)
        self.assertTrue(report['firmaIntegra'],report)
        self.assertTrue(report['cadenaConfiable'],report)
        self.assertTrue(report['identidadCertificado'],report)
        self.assertFalse(report['fechaConfiable'])

    def test_personal_certificate_requires_explicit_issuer_policy_not_a_matching_subject_alone(self):
        policy={'raizSha256':sha256(self.roots[0]).hexdigest(),
            'politicaOid':'1.3.6.1.4.1.55555.9','referencia':'SYNTHETIC_CPS_NOT_INSTITUTIONAL'}
        report=self.verify(politicas_titular=[policy])
        self.assertEqual(report['rolCertificado'],'FIRMANTE_TITULAR',report)
        self.assertTrue(report['participacionElectronicaAcreditada'],report)
        self.assertFalse(report['fechaConfiable'])
        for change in ({'politicaOid':'1.3.6.1.4.1.55555.10'},{'raizSha256':'f'*64}):
            self.assertFalse(self.verify(politicas_titular=[{**policy,**change}])['participacionElectronicaAcreditada'])

    def timestamped(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
        from asn1crypto import x509 as ax509,keys
        from pyhanko.sign import signers,timestamps
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cert=(x509.CertificateBuilder().subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME,'SYNTHETIC TSA ONLY')]))
            .issuer_name(self.root_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(self.now-timedelta(days=1)).not_valid_after(self.now+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]),True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.root_key.public_key()),False)
            .sign(self.root_key,hashes.SHA256()))
        tsa=timestamps.DummyTimeStamper(ax509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
            keys.PrivateKeyInfo.load(key.private_bytes(serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,serialization.NoEncryption())))
        return signers.sign_pdf(IncrementalPdfFileWriter(BytesIO(self.original)),
            signature_meta=signers.PdfSignatureMetadata(field_name='SyntheticSignature'),
            signer=self.signer,timestamper=tsa).getvalue()

    def test_contract_signature_verifies_full_synthetic_chain_timestamp_and_dossier_binding(self):
        from ..evidencia import verificar_firma_contractual,FirmaContractualVerificada
        signed=self.timestamped()
        when=datetime.now(timezone.utc)+timedelta(seconds=2)
        policy={'raizSha256':sha256(self.roots[0]).hexdigest(),'politicaOid':'1.3.6.1.4.1.55555.9',
            'referencia':'SYNTHETIC_CPS_NOT_INSTITUTIONAL'}
        ctx=context(self.original,signed,when)
        proof=verificar_firma_contractual(original=self.original,firmado=signed,transaccion='SYNTHETIC-TX',
            documento_identidad='SYNTHETIC-ID',contexto=ctx,raices=self.roots,crls=self.crls,
            politicas_titular=[policy],momento=when)
        self.assertIs(type(proof),FirmaContractualVerificada)
        self.assertEqual(proof.solicitud_id,ctx['solicitudId'])
        self.assertEqual(proof.firmado_sha256,sha256(signed).hexdigest())

    def test_embedded_certificate_does_not_become_trust_root(self):
        report=self.verify(raices=())
        self.assertFalse(report['cadenaConfiable'])
        self.assertEqual(report['codigo'],'RAICES_DE_CONFIANZA_NO_CONFIGURADAS')

    def test_provider_certificate_can_be_valid_without_identifying_holder(self):
        report=self.verify(documento_identidad='ANOTHER-ID')
        self.assertTrue(report['firmaIntegra'],report)
        self.assertTrue(report['cadenaConfiable'],report)
        self.assertFalse(report['identidadCertificado'])
        self.assertFalse(report['participacionElectronicaAcreditada'])
        self.assertEqual(report['rolCertificado'],'NO_DETERMINADO')

    def test_expired_certificate_is_not_trusted(self):
        with patch('apps.integraciones.autentic.pdf_criptografia.datetime') as clock:
            clock.now.return_value=self.now+timedelta(days=3)
            report=verificar_pdf(self.original,self.signed,'SYNTHETIC-ID',raices=self.roots,crls=self.crls)
        self.assertFalse(report['cadenaConfiable'],report)
        self.assertFalse(report['identidadCertificado'],report)

    def test_foreign_identity_rewritten_original_or_unsigned_document_are_not_validated(self):
        self.assertFalse(self.verify(documento_identidad='ANOTHER-ID')['identidadCertificado'])
        self.assertFalse(self.verify(original=self.original+b'\nCHANGED')['correspondenciaOriginal'])
        self.assertFalse(self.verify(firmado=self.original)['firmaIntegra'])

    def test_tampering_in_signed_revision_is_rejected(self):
        changed=self.signed.replace(b'/MediaBox [ 0 0 100 100 ]',b'/MediaBox [ 0 0 101 100 ]',1)
        self.assertNotEqual(changed,self.signed)
        self.assertFalse(self.verify(firmado=changed)['firmaIntegra'])

    def test_revoked_certificate_is_not_trusted_and_cannot_identify_signer(self):
        report=self.verify(crls=self.revoked_crls)
        self.assertFalse(report['cadenaConfiable'],report)
        self.assertFalse(report['identidadCertificado'],report)

    def test_cryptographically_signed_incremental_page_change_does_not_match_accepted_original(self):
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.pdf_utils import generic
        from pyhanko.sign import signers
        writer=IncrementalPdfFileWriter(BytesIO(self.original))
        page_ref,_=writer.find_page_for_modification(0);page=page_ref.get_object()
        page['/MediaBox']=generic.ArrayObject([generic.NumberObject(v) for v in (0,0,101,100)])
        writer.update_container(page)
        changed=signers.sign_pdf(writer,signature_meta=signers.PdfSignatureMetadata(
            field_name='SyntheticSignature'),signer=self.signer).getvalue()
        self.assertTrue(changed.startswith(self.original))
        report=self.verify(firmado=changed)
        self.assertFalse(report['correspondenciaOriginal'],report)

    def test_custom_crypto_handler_cannot_leak_certificate_subjects_to_general_logs(self):
        import logging
        stream=BytesIO()
        logger=logging.getLogger('pyhanko.sign.validation')
        with patch.object(logger,'handlers',[logging.StreamHandler(stream)]):
            report=self.verify()
        self.assertEqual(report['codigo'],'LOG_CRIPTOGRAFICO_NO_AISLADO')
        self.assertEqual(stream.getvalue(),b'')


class RewriteContentTests(unittest.TestCase):
    """Reserialization is allowed; overlays, resources and annotations are not ignored."""
    def build(self, *, change=None):
        from pypdf.generic import NameObject, DecodedStreamObject, DictionaryObject
        writer=PdfWriter();page=writer.add_blank_page(width=100,height=100)
        stream=DecodedStreamObject();stream.set_data(b'q 0 0 5 5 re f Q')
        page[NameObject('/Contents')]=writer._add_object(stream)
        if change:change(writer,page)
        out=BytesIO();writer.write(out);return out.getvalue()

    def compare(self, original, signed):
        from ..pdf_criptografia import comparar_contenido_pdf
        return comparar_contenido_pdf(original,signed)

    def test_rewritten_serialization_can_preserve_content_without_identical_hashes(self):
        original=self.build()
        signed=self.build(change=lambda writer,page:writer.add_metadata({'/Producer':'SYNTHETIC REWRITE'}))
        self.assertNotEqual(sha256(original).hexdigest(),sha256(signed).hexdigest())
        self.assertFalse(signed.startswith(original))
        report=self.compare(original,signed)
        self.assertTrue(report['correspondenciaOriginal'],report)

    def test_same_extracted_text_does_not_hide_graphical_overlay_or_replaced_content(self):
        from pypdf.generic import NameObject,DecodedStreamObject
        def overlay(writer,page):
            stream=DecodedStreamObject();stream.set_data(b'q 0 0 100 100 re f Q')
            page[NameObject('/Contents')]=writer._add_object(stream)
        original=self.build();signed=self.build(change=overlay)
        self.assertFalse(self.compare(original,signed)['correspondenciaOriginal'])

    def test_added_page_annotation_changed_geometry_or_active_content_are_not_equivalent(self):
        from pypdf.generic import NameObject,DictionaryObject,TextStringObject,ArrayObject,NumberObject
        changes=[lambda writer,page:writer.add_blank_page(width=100,height=100),
            lambda writer,page:page.__setitem__(NameObject('/Rotate'),NumberObject(90)),
            lambda writer,page:writer.add_js('SYNTHETIC_NOT_EXECUTED'),
            lambda writer,page:page.__setitem__(NameObject('/Annots'),ArrayObject([
                writer._add_object(DictionaryObject({NameObject('/Subtype'):NameObject('/Text'),
                    NameObject('/Contents'):TextStringObject('SYNTHETIC CHANGE')}))]))]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(self.compare(self.build(),self.build(change=change))['correspondenciaOriginal'])

    def test_changed_render_resource_is_not_hidden_by_unchanged_text_or_operators(self):
        from pypdf.generic import NameObject,DictionaryObject,NumberObject
        def resource(writer,page):
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/ExtGState'):DictionaryObject({
                NameObject('/GS'):DictionaryObject({NameObject('/ca'):NumberObject(0)})})})
        self.assertFalse(self.compare(self.build(),self.build(change=resource))['correspondenciaOriginal'])

    def test_seal_without_provisioned_trust_never_accepts_pdf_embedded_root(self):
        from ..pdf_criptografia import verificar_sello_proveedor
        report=verificar_sello_proveedor(self.build(),self.build())
        self.assertFalse(report['cadenaConfiable'])
        self.assertEqual(report['codigo'],'RAICES_DE_CONFIANZA_NO_CONFIGURADAS')


class AutenticTransformationTests(unittest.TestCase):
    def pair(self, *, occupied=False, altered=False, extra_operator=False):
        from pypdf import PdfReader
        from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
        writer=PdfWriter()
        for _ in range(4):
            page=writer.add_blank_page(612,792)
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject()})
            content=DecodedStreamObject()
            content.set_data(b'q 0 0 0 rg 335 780 10 6 re f Q' if occupied else b'q Q')
            page[NameObject('/Contents')]=writer._add_object(content)
        stream=BytesIO();writer.write(stream);original=stream.getvalue()
        target=PdfWriter();target.append(PdfReader(BytesIO(original)))
        identifier='11111111-1111-4111-8111-111111111111'
        text=('Firmado electr\u00f3nicamente. Documento No. '+identifier).encode('cp1252')
        footer=(b'q BT 1 0 0 1 332 781 Tm ET /Span <</MCID 0>> BDC BT 1 0 0 1 332 781 Tm ET '
            b'q 1 1 1 rg 332 778.9 241.75 8.4 re f 0 g Q BT 1 0 0 1 332 781 Tm /Xi0 6 Tf '
            b'0.34902 0.34902 0.39608 rg ('+text+b') Tj 0 g ET EMC BT 1 0 0 1 573.75 781 Tm ET Q')
        for number,page in enumerate(target.pages):
            page['/Resources']['/Font'][NameObject('/Xi0')]=DictionaryObject({
                NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),
                NameObject('/BaseFont'):NameObject('/Helvetica'),NameObject('/Encoding'):NameObject('/WinAnsiEncoding')})
            body=page.get_contents().get_data()
            if altered and number==0:body+=b' q 0 0 4 4 re f Q'
            content=DecodedStreamObject();content.set_data(b'q '+body+b' Q '+footer+(b' q Q' if extra_operator else b''))
            page[NameObject('/Contents')]=target._add_object(content)
        target.add_blank_page(612,792)
        out=BytesIO();target.write(out)
        return original,out.getvalue()

    def analyze(self,**kwargs):
        from ..pdf_criptografia import comparar_contenido_pdf
        a,b=self.pair(**kwargs);return comparar_contenido_pdf(a,b)

    def test_four_to_five_safe_mark_proves_original_visible_but_does_not_auto_accept_annex(self):
        report=self.analyze();proof=report['transformacionAutentic']
        self.assertTrue(proof['reconocida'],proof)
        self.assertTrue(proof['contenidoOriginalVisible'],proof)
        self.assertFalse(proof['anexoPermitido']);self.assertFalse(report['correspondenciaOriginal'])
        self.assertEqual(proof['codigo'],'ANEXO_REQUIERE_VINCULO_VERIFICABLE')

    def test_white_mark_cannot_hide_an_existing_graphic_even_if_all_original_operators_remain(self):
        proof=self.analyze(occupied=True)['transformacionAutentic']
        self.assertTrue(proof['reconocida']);self.assertTrue(proof['operadoresOriginalesConservados'])
        self.assertFalse(proof['contenidoOriginalVisible'])
        self.assertEqual(proof['codigo'],'AUTENTIC_MARCA_OCULTA_O_CAMBIA_GRAFICA_ORIGINAL')

    def test_modified_contract_or_additional_graphical_operator_is_not_the_known_transform(self):
        for change in ({'altered':True},{'extra_operator':True}):
            proof=self.analyze(**change)['transformacionAutentic']
            self.assertFalse(proof['reconocida']);self.assertFalse(proof['anexoPermitido'])


class SealRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa,ed25519
        from cryptography.x509.oid import NameOID
        from asn1crypto import x509 as asn1x509,keys
        from pyhanko.sign import signers
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko_certvalidator.registry import SimpleCertificateStore
        CryptographyTests.setUpClass();cls.original=CryptographyTests.original
        cls.now=datetime.now(timezone.utc);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'SYNTHETIC PROVIDER SEAL ONLY')])
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(cls.now-timedelta(days=1))
            .not_valid_after(cls.now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
            .add_extension(x509.KeyUsage(True,True,False,False,False,False,False,False,False),True)
            .sign(key,hashes.SHA256()))
        cls.cert=cert.public_bytes(serialization.Encoding.DER)
        cls.spki=sha256(key.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
        signer=signers.SimpleSigner(signing_cert=asn1x509.Certificate.load(cls.cert),
            signing_key=keys.PrivateKeyInfo.load(key.private_bytes(serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,serialization.NoEncryption())),cert_registry=SimpleCertificateStore())
        cls.signed=signers.sign_pdf(IncrementalPdfFileWriter(BytesIO(cls.original)),
            signature_meta=signers.PdfSignatureMetadata(field_name='SyntheticSignature'),signer=signer).getvalue()
        cls.authority=ed25519.Ed25519PrivateKey.generate()
        cls.public=cls.authority.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)

    def envelope(self, *, revoked=False):
        import base64
        p={'version':'AUTENTIC_SELLO_REGISTRO_V1','proveedor':'AUTENTIC','ambiente':'DEV',
            'referencia':'SYNTHETIC AUTHORITY NOT INSTITUTIONAL','emitidoEn':(self.now-timedelta(minutes=1)).isoformat(),
            'vigenteHasta':(self.now+timedelta(hours=1)).isoformat(),'certificados':[{
                'certificadoSha256':sha256(self.cert).hexdigest(),'spkiSha256':self.spki,
                'estado':'REVOCADO' if revoked else 'ACTIVO','vigenteDesde':(self.now-timedelta(hours=1)).isoformat(),
                'vigenteHasta':(self.now+timedelta(hours=1)).isoformat(),
                'revocadoEn':(self.now-timedelta(seconds=1)).isoformat() if revoked else None,
                'fuenteIndependiente':'SYNTHETIC OUT-OF-BAND CERTIFICATE REFERENCE'}]}
        raw=json.dumps(p,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
        return {'payload':p,'firmaBase64':base64.b64encode(self.authority.sign(raw)).decode()}

    def verify(self, **kwargs):
        from ..pdf_criptografia import verificar_sello_proveedor
        return verificar_sello_proveedor(self.original,self.signed,momento=self.now,
            registro_sello=kwargs.pop('registro_sello',self.envelope()),
            autoridad_sello=kwargs.pop('autoridad_sello',self.public),**kwargs)

    def test_private_registry_loader_rejects_duplicate_json_same_file_and_invalid_authority(self):
        import base64
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from ..pdf_criptografia import cargar_registro_sello
        with TemporaryDirectory() as folder:
            root=Path(folder);registry=root/'registry.json';authority=root/'authority.pub'
            registry.write_text(json.dumps(self.envelope()),encoding='utf-8')
            authority.write_bytes(base64.b64encode(self.public))
            registry.chmod(0o600);authority.chmod(0o600)
            envelope,key=cargar_registro_sello(str(registry),str(authority))
            self.assertTrue(self.verify(registro_sello=envelope,autoridad_sello=key)['cadenaConfiable'])
            for paths in ((registry,registry),(Path('relative.json'),authority)):
                with self.assertRaisesRegex(ValueError,'CUSTODIA_REGISTRO_SELLO_INVALIDA'):
                    cargar_registro_sello(*(str(p) for p in paths))
            registry.write_text('{"payload":{},"payload":{},"firmaBase64":""}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'CUSTODIA_REGISTRO_SELLO_INVALIDA'):
                cargar_registro_sello(str(registry),str(authority))
            registry.write_text(json.dumps(self.envelope()),encoding='utf-8')
            authority.write_bytes(b'not-a-public-key')
            with self.assertRaisesRegex(ValueError,'CUSTODIA_REGISTRO_SELLO_INVALIDA'):
                cargar_registro_sello(str(registry),str(authority))

    def test_signed_registry_rotation_unknown_certificate_wrong_spki_and_duplicates_fail_closed(self):
        import base64
        def resign(envelope):
            envelope['firmaBase64']=base64.b64encode(self.authority.sign(json.dumps(envelope['payload'],
                sort_keys=True,separators=(',',':'),ensure_ascii=True).encode())).decode()
            return envelope
        for field,expected in (('certificadoSha256','SELLO_NO_REGISTRADO'),('spkiSha256','SELLO_CLAVE_NO_CORRESPONDE')):
            envelope=self.envelope();envelope['payload']['certificados'][0][field]='0'*64
            self.assertEqual(self.verify(registro_sello=resign(envelope))['codigo'],expected)
        envelope=self.envelope();envelope['payload']['certificados']*=2
        self.assertEqual(self.verify(registro_sello=resign(envelope))['codigo'],'REGISTRO_SELLO_CERTIFICADOS_INVALIDOS')

    def test_independently_signed_registry_can_pin_a_self_signed_seal_without_claiming_personal_identity_or_tsa(self):
        report=self.verify()
        self.assertTrue(report['firmaIntegra'],report);self.assertTrue(report['cadenaConfiable'],report)
        self.assertFalse(report['fechaConfiableTSA'])
        self.assertEqual(report['revocacion'],'NO_REVOCADO_SEGUN_REGISTRO_INSTITUCIONAL_VIGENTE')

    def test_revoked_or_unknown_rotated_certificate_does_not_become_trusted(self):
        self.assertEqual(self.verify(registro_sello=self.envelope(revoked=True))['codigo'],'SELLO_REVOCADO')
        envelope=self.envelope();envelope['payload']['certificados'][0]['estado']='REVOCADO'
        self.assertEqual(self.verify(registro_sello=envelope)['codigo'],'REGISTRO_SELLO_FIRMA_O_CONTRATO_INVALIDO')

    def test_other_authority_environment_expiry_and_pdf_supplied_root_cannot_authorize_trust(self):
        from ..pdf_criptografia import verificar_sello_proveedor
        self.assertFalse(self.verify(autoridad_sello=b'x'*32)['cadenaConfiable'])
        self.assertFalse(self.verify(ambiente='PRODUCCION')['cadenaConfiable'])
        self.assertFalse(verificar_sello_proveedor(self.original,self.signed,raices=[self.cert],momento=self.now)['cadenaConfiable'])
        with patch('apps.integraciones.autentic.pdf_criptografia.datetime') as clock:
            clock.now.return_value=self.now+timedelta(hours=2)
            report=verificar_sello_proveedor(self.original,self.signed,registro_sello=self.envelope(),autoridad_sello=self.public)
        self.assertFalse(report['cadenaConfiable']);self.assertEqual(report['codigo'],'REGISTRO_SELLO_VENCIDO_O_SIN_ZONA')
