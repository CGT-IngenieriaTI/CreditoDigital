"""Official consult contract and recovery, using synthetic documents and no network."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
import traceback
from threading import Barrier
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
import requests

from ..cliente import AutenticDEVClient, ReciboFirma
from ..contratos import AutenticError, ConfiguracionSTAGING, DEV_URL, TOKEN_URL
from ..operaciones import RegistroFirmas
from .test_cliente import Session, Response, token, recibo, solicitud


class ConsultaConciliacionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.crypto=Fernet(Fernet.generate_key())
        self.encrypt=lambda value:self.crypto.encrypt(value.encode()).decode()
        self.decrypt=lambda value:self.crypto.decrypt(value.encode()).decode()
        self.registry=RegistroFirmas(self.db,cifrar=self.encrypt,descifrar=self.decrypt)
        self.request=solicitud()

    def client(self,*responses):
        session=Session(*responses)
        client=AutenticDEVClient(ConfiguracionSTAGING('SYNTHETIC_CLIENT','SYNTHETIC_SECRET',
            permitir_documentos=True),session=session)
        self.addCleanup(client.close)
        return client,session

    def sign(self,client):
        return client.firmar(self.request,clave='once',registro=self.registry,
            referencia_autorizacion='SYNTHETIC_PERSONAL_ACT')

    def uncertain(self):
        self.registry.preparar('once',self.request.fingerprint)
        self.registry.iniciar('once');self.registry.terminar('once','INDETERMINADA')

    def evidence(self,result='PROCESADA'):
        return {'version':'AUTENTIC_CONCILIACION_V1','clave':'once','fingerprint':self.request.fingerprint,
            'ambiente':'DEV','resultado':result,'origen':'CONFIRMACION_AUTENTIC','fuenteReferencia':'SYNTHETIC-TICKET',
            'evidenciaSha256':'a'*64,'recibidaEn':datetime.now(timezone.utc).isoformat(),
            'verificadorReferenciaSha256':'b'*64,
            'transaccionProveedorSha256':sha256(recibo()['transactionId'].encode()).hexdigest() if result=='PROCESADA' else ''}

    def test_consult_uses_only_transaction_id_and_correct_profile_without_signing(self):
        c,s=self.client(Response(token()),Response({'transactionId':'PROVIDER-ID','status':'SIGNED'}))
        result=c.consultar_estado('PROVIDER-ID')
        self.assertEqual(s.calls[-1][0:2],('POST',DEV_URL+'/v2/consult/'))
        self.assertEqual(s.calls[-1][2]['json'],{'transactionId':'PROVIDER-ID'})
        self.assertEqual(s.calls[0][2]['json']['audience'],'stag-api.autenticsign.com')
        self.assertFalse(result.firma_acreditada);self.assertFalse(result.autoriza_reenvio)
        self.assertEqual(result.estado,'RESPUESTA_RECIBIDA_NO_CONCILIADA')
        self.assertEqual(c.auditoria_firma['peticiones'],0)

    def test_missing_identifier_fails_before_oauth_and_has_no_alternative_search(self):
        c,s=self.client()
        for value in (None,'',' ',' padded','bad\n',123,{'docId':'SYNTHETIC'}):
            with self.subTest(value=value),self.assertRaisesRegex(AutenticError,'TRANSACTION_ID_PROVEEDOR_REQUERIDO'):
                c.consultar_estado(value)
        self.assertEqual(s.calls,[])

    def test_consult_404_does_not_prove_original_not_processed_or_release_journal(self):
        self.uncertain()
        c,s=self.client(Response(token()),Response(b'',404,mime='text/html'))
        result=c.consultar_estado('PROVIDER-ID')
        self.assertEqual(result.estado,'NO_ENCONTRADA_NO_AUTORIZA_REENVIO')
        self.assertFalse(result.autoriza_reenvio)
        before=len(s.calls)
        with self.assertRaisesRegex(AutenticError,'REENVIO_BLOQUEADO'):
            self.sign(c)
        self.assertEqual(len(s.calls),before)
        self.assertEqual(self.registry.preparar('once',self.request.fingerprint).estado,'INDETERMINADA')

    def test_consult_never_invents_response_schema_or_terminal_business_states(self):
        c,_=self.client(Response(token()),Response({'vendorNewState':'UNSPECIFIED','operationCode':1000}))
        result=c.consultar_estado('PROVIDER-ID')
        self.assertEqual(result.estado,'RESPUESTA_RECIBIDA_NO_CONCILIADA')
        self.assertFalse(result.firma_acreditada)
        for body in ({'transactionId':'OTHER'},[],b'{"transactionId":"X","transactionId":"X"}',b'not-json'):
            with self.subTest(body=body):
                c,_=self.client(Response(token()),Response(body))
                with self.assertRaisesRegex(AutenticError,'CONTRATO_CONSULTA_NO_ACREDITADO'):
                    c.consultar_estado('PROVIDER-ID')

    def test_consult_http_errors_preserve_status_without_new_signature(self):
        for status in (400,401,500):
            with self.subTest(status=status):
                c,s=self.client(Response(token()),Response({'error':'PRIVATE'},status))
                with self.assertRaises(AutenticError):c.consultar_estado('PROVIDER-ID')
                self.assertEqual(c.auditoria_consulta['http'],status)
                self.assertEqual(c.auditoria_firma['peticiones'],0)
                self.assertEqual(len(s.calls),2)

    def test_network_exception_chain_is_preserved_without_exposing_exception_text(self):
        secrets='SYNTHETIC_SECRET TOKEN-SINTETICO DOCUMENTO-FICTICIO EVIDENCIA-SINTETICA-NO-OTP PRIVATE_PDF'
        error=requests.ConnectionError(secrets)
        error.__cause__=socket.gaierror(-2,secrets)
        c,s=self.client(Response(token()),error)
        try:self.sign(c)
        except AutenticError as caught:
            rendered=''.join(traceback.format_exception(caught))
            self.assertNotIn(secrets,rendered)
            self.assertIs(caught.__cause__.original.__cause__.original,error)
        else:self.fail('uncertain transport must fail')
        diagnostic=json.loads(self.decrypt(c.diagnostico_cifrado(self.encrypt,solicitud_id='SYNTHETIC',operacion_id='once')))
        text=json.dumps(diagnostic)
        for private in secrets.split():self.assertNotIn(private,text)
        event=diagnostic['eventos'][-1]
        self.assertEqual(event['excepcion']['fase'],'DNS')
        self.assertFalse(event['respuestaHttpRecibida'])
        self.assertIsNone(event['excepcion']['transmisionOriginalAcreditada'])
        self.assertEqual(event['fingerprint'],self.request.fingerprint)
        self.assertEqual(event['operacionReferenciaSha256'],sha256(b'once').hexdigest())
        self.assertIsInstance(event['duracionMs'],int)
        self.assertEqual(len(s.calls),2)

    def test_timeout_phase_is_recorded_without_authorizing_replay(self):
        for error,phase in ((requests.ConnectTimeout('PRIVATE'),'TIMEOUT_CONEXION'),
                (requests.ReadTimeout('PRIVATE'),'TIMEOUT_LECTURA')):
            with self.subTest(phase=phase):
                db=sqlite3.connect(':memory:')
                with closing(db):
                    registry=RegistroFirmas(db);c,s=self.client(Response(token()),error)
                    with self.assertRaises(AutenticError):c.firmar(self.request,clave='once',registro=registry,referencia_autorizacion='TEST')
                    self.assertEqual(c._diagnosticos_http[-1]['excepcion']['fase'],phase)
                    self.assertEqual(registry.preparar('once',self.request.fingerprint).estado,'INDETERMINADA')
                    self.assertEqual(len(s.calls),2)

    def test_valid_reply_is_custodied_before_download_and_replay_has_no_http(self):
        c,s=self.client(Response(token()),Response(recibo(),201))
        first=self.sign(c);second=self.sign(c)
        self.assertEqual(first,second);self.assertEqual(len(s.calls),2)
        self.assertEqual(self.registry.recuperar_recibo('once'),first)
        self.assertEqual(self.registry.preparar('once',self.request.fingerprint).estado,'FIRMA_REPORTADA_NO_VERIFICADA')

    def test_valid_reply_survives_journal_failure_in_existing_encrypted_diagnostics(self):
        c,s=self.client(Response(token()),Response(recibo(),201))
        with patch.object(self.registry,'terminar',side_effect=sqlite3.OperationalError('PRIVATE')):
            with self.assertRaisesRegex(AutenticError,'ENVIO_INDETERMINADO'):self.sign(c)
        encrypted=c.diagnostico_cifrado(self.encrypt,solicitud_id='SYNTHETIC',operacion_id='once')
        saved=json.loads(self.decrypt(encrypted))['reciboValidadoRecuperable']
        self.assertEqual(saved['transaccion'],recibo()['transactionId'])
        self.assertNotIn(recibo()['urlDocuments'],encrypted)
        self.assertEqual(len(s.calls),2)
        with self.assertRaisesRegex(AutenticError,'REENVIO_BLOQUEADO'):self.sign(c)
        self.assertEqual(len(s.calls),2)

    def test_documented_business_error_is_terminal_but_not_permission_to_retry(self):
        body={'transactionId':'PROVIDER-ERROR-ID','operationCode':2000,'operationMsg':'PDF header signature not found.',
            'processedDate':'2026-11-09T00:00:00Z','type':'reply','urlDocuments':''}
        c,s=self.client(Response(token()),Response(body,500))
        with self.assertRaisesRegex(AutenticError,'FIRMA_RECHAZADA_DOCUMENTADA'):self.sign(c)
        self.assertEqual(self.registry.preparar('once',self.request.fingerprint).estado,'FALLO_DEFINITIVO_DOCUMENTADO')
        with self.assertRaisesRegex(AutenticError,'REENVIO_BLOQUEADO'):self.sign(c)
        self.assertEqual(len(s.calls),2)

    def test_201_or_http_errors_without_a_valid_contract_remain_uncertain(self):
        for response in (Response({'operationCode':1000},201),Response({'operationCode':2000},201),
                Response({'error':'PRIVATE'},400),Response({},401),Response({},500),Response(b'{broken',201)):
            with self.subTest(status=response.status_code):
                with closing(sqlite3.connect(':memory:')) as db:
                    registry=RegistroFirmas(db);c,s=self.client(Response(token()),response)
                    with self.assertRaises(AutenticError):c.firmar(self.request,clave='once',registro=registry,referencia_autorizacion='TEST')
                    self.assertEqual(registry.preparar('once',self.request.fingerprint).estado,'INDETERMINADA')
                    self.assertEqual(len(s.calls),2)

    def test_verified_processed_reconciliation_recovers_receipt_without_resending(self):
        self.uncertain();evidence=self.evidence();receipt=ReciboFirma.desde_respuesta(recibo(),'DEV')
        first=self.registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True,recibo=receipt)
        repeated=self.registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True,recibo=receipt)
        self.assertEqual(first.estado,'CONCILIADA_PROCESADA');self.assertEqual(first,repeated)
        c,s=self.client();self.assertEqual(self.sign(c),receipt);self.assertEqual(s.calls,[])
        row=self.db.execute('SELECT cifrado FROM autentic_recibos WHERE clave=?',('@conciliacion:once',)).fetchone()
        saved=self.registry._abrir_conciliacion('once',self.request.fingerprint,row[0])
        self.assertEqual(saved['estadoAnterior'],'INDETERMINADA');self.assertEqual(saved['evidencia'],evidence)

    def test_verified_not_processed_reconciliation_keeps_duplicate_guards(self):
        self.uncertain();evidence=self.evidence('NO_PROCESADA')
        state=self.registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True)
        self.assertEqual(state.estado,'CONCILIADA_NO_PROCESADA')
        c,s=self.client()
        with self.assertRaisesRegex(AutenticError,'REENVIO_BLOQUEADO'):self.sign(c)
        with self.assertRaisesRegex(AutenticError,'ENVIO_DUPLICADO_OTRA_CLAVE'):
            self.registry.preparar('another',self.request.fingerprint)
        self.assertEqual(s.calls,[])

    def test_unverified_wrong_scope_or_conflicting_evidence_never_releases_claim(self):
        self.uncertain();receipt=ReciboFirma.desde_respuesta(recibo(),'DEV')
        evidence=self.evidence()
        cases=({**evidence,'fingerprint':'c'*64},{**evidence,'ambiente':'PRODUCCION'},
            {**evidence,'extra':'PRIVATE'},{**evidence,'recibidaEn':'2020-01-01'})
        for value in cases:
            with self.assertRaisesRegex(AutenticError,'EVIDENCIA_CONCILIACION_NO_VERIFICADA'):
                self.registry.conciliar('once',evidencia=value,verificar_evidencia=lambda _:True,recibo=receipt)
        with self.assertRaisesRegex(AutenticError,'EVIDENCIA_CONCILIACION_NO_VERIFICADA'):
            self.registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:False,recibo=receipt)
        self.assertEqual(self.registry.preparar('once',self.request.fingerprint).estado,'INDETERMINADA')
        self.registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True,recibo=receipt)
        with self.assertRaisesRegex(AutenticError,'CONCILIACION_EXISTENTE_CONFLICTO'):
            self.registry.conciliar('once',evidencia={**evidence,'evidenciaSha256':'c'*64},verificar_evidencia=lambda _:True,recibo=receipt)

    def test_reconciliation_remains_durable_after_reopening_the_same_journal(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'journal.sqlite3';evidence=self.evidence('NO_PROCESADA')
            with closing(sqlite3.connect(path)) as db:
                registry=RegistroFirmas(db,cifrar=self.encrypt,descifrar=self.decrypt)
                registry.preparar('once',self.request.fingerprint);registry.iniciar('once');registry.terminar('once','INDETERMINADA')
                registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True)
            with closing(sqlite3.connect(path)) as db:
                registry=RegistroFirmas(db,cifrar=self.encrypt,descifrar=self.decrypt)
                self.assertEqual(registry.preparar('once',self.request.fingerprint).estado,'CONCILIADA_NO_PROCESADA')
                with self.assertRaisesRegex(AutenticError,'REENVIO_BLOQUEADO'):registry.iniciar('once')

    def test_original_provider_transaction_cannot_be_replaced_or_erased(self):
        receipt=ReciboFirma.desde_respuesta(recibo(),'DEV')
        self.registry.preparar('once',self.request.fingerprint);self.registry.iniciar('once')
        self.registry.terminar('once','INDETERMINADA','PREVIOUS-PROVIDER-ID')
        with self.assertRaisesRegex(AutenticError,'TRANSACCION_DIFIERE'):
            self.registry.conciliar('once',evidencia=self.evidence(),verificar_evidencia=lambda _:True,recibo=receipt)
        result=self.registry.conciliar('once',evidencia=self.evidence('NO_PROCESADA'),verificar_evidencia=lambda _:True)
        self.assertEqual(result.transaccion,'PREVIOUS-PROVIDER-ID')
        saved=self.db.execute('SELECT cifrado FROM autentic_recibos WHERE clave=?',('@conciliacion:once',)).fetchone()[0]
        self.assertEqual(self.registry._abrir_conciliacion('once',self.request.fingerprint,saved)['transaccionAnterior'],result.transaccion)

    def test_reconciliation_cannot_override_active_dispatch_or_accept_tampered_custody(self):
        evidence=self.evidence('NO_PROCESADA')
        self.registry.preparar('once',self.request.fingerprint);self.registry.iniciar('once')
        with self.assertRaisesRegex(AutenticError,'CONCILIACION_REQUIERE_ENVIO'):
            self.registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True)
        self.registry.terminar('once','INDETERMINADA')
        self.registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True)
        self.db.execute('UPDATE autentic_recibos SET cifrado=? WHERE clave=?',('tampered','@conciliacion:once'))
        self.db.commit()
        with self.assertRaisesRegex(AutenticError,'CONCILIACION_NO_INTEGRA'):
            self.registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True)
        self.assertEqual(self.registry.preparar('once',self.request.fingerprint).estado,'CONCILIADA_NO_PROCESADA')

    def test_concurrent_reconciliation_is_durable_idempotent_and_does_not_duplicate_claims(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'journal.sqlite3';evidence=self.evidence('NO_PROCESADA');barrier=Barrier(2)
            with closing(sqlite3.connect(path)) as db:
                registry=RegistroFirmas(db,cifrar=self.encrypt,descifrar=self.decrypt)
                registry.preparar('once',self.request.fingerprint);registry.iniciar('once');registry.terminar('once','INDETERMINADA')
            def reconcile(_):
                with closing(sqlite3.connect(path,timeout=10)) as db:
                    registry=RegistroFirmas(db,cifrar=self.encrypt,descifrar=self.decrypt)
                    barrier.wait(timeout=10)
                    return registry.conciliar('once',evidencia=evidence,verificar_evidencia=lambda _:True).estado
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(reconcile,range(2))),['CONCILIADA_NO_PROCESADA']*2)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM autentic_operaciones').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM autentic_recibos').fetchone()[0],1)
