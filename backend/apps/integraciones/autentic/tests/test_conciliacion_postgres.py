"""Existing dossier journal, disposable PostgreSQL and synthetic custody only."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante
from apps.utils.models import AuditLog
from apps.xcore_consumo.models import SolicitudConsumo
from apps.xcore_consumo.services.otp_crypto import decrypt_text
from ..cliente import ReciboFirma
from ..contratos import AutenticError
from ..operaciones import RegistroFirmasExpediente
from .test_cliente import recibo


@override_settings(OTP_AES_KEY_B64=base64.b64encode(b'x'*32).decode())
class ConciliacionPostgresTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor!='postgresql':self.skipTest('Disposable PostgreSQL required')
        holder=Solicitante.objects.create(tipo_identificacion='CC',numero_identificacion='900000001',
            primer_apellido='SYNTHETIC',fecha_expedicion=date(2000,1,1))
        self.case=Solicitud.objects.create(solicitante=holder,producto='CREDIHOY_UNICO')
        self.key=str(uuid4());self.fingerprint='a'*64
        self.original={'acceptedSnapshot':{'sha256':'b'*64},'preservedStudy':{'sha256':'c'*64},
            'credihoy_autentic_envios_v1':{self.key:{'vinculo':{'solicitudId':str(self.case.pk),
                'ambienteProveedor':'DEV','pdfSha256':'d'*64},'estado':'INDETERMINADA',
                'diagnosticoHttpCifrado':'PRESERVED-SYNTHETIC-DIAGNOSTIC',
                'journalFirma':{'clave':self.key,'fingerprint':self.fingerprint,'estado':'INDETERMINADA','transaccion':''}}}}
        self.detail=SolicitudConsumo.objects.create(solicitud=self.case,orchestration_data=deepcopy(self.original))
        self.journal=RegistroFirmasExpediente(self.case.pk,self.key)
        self.evidence={'version':'AUTENTIC_CONCILIACION_V1','clave':self.key,'fingerprint':self.fingerprint,
            'ambiente':'DEV','resultado':'PROCESADA','origen':'CONFIRMACION_AUTENTIC','fuenteReferencia':'SYNTHETIC-TICKET',
            'evidenciaSha256':'e'*64,'recibidaEn':timezone.now().isoformat(),'verificadorReferenciaSha256':'f'*64,
            'transaccionProveedorSha256':sha256(recibo()['transactionId'].encode()).hexdigest()}

    def test_processed_reconciliation_preserves_prior_evidence_and_custodies_receipt_once(self):
        receipt=ReciboFirma.desde_respuesta(recibo(),'DEV')
        for _ in range(2):
            result=self.journal.conciliar(self.key,evidencia=self.evidence,verificar_evidencia=lambda _:True,recibo=receipt)
            self.assertEqual(result.estado,'CONCILIADA_PROCESADA')
        self.assertEqual(self.journal.recuperar_recibo(self.key),receipt)
        self.detail.refresh_from_db();data=self.detail.orchestration_data;entry=data['credihoy_autentic_envios_v1'][self.key]
        self.assertEqual(data['acceptedSnapshot'],self.original['acceptedSnapshot'])
        self.assertEqual(data['preservedStudy'],self.original['preservedStudy'])
        self.assertEqual(entry['diagnosticoHttpCifrado'],'PRESERVED-SYNTHETIC-DIAGNOSTIC')
        self.assertEqual(entry['vinculo'],self.original['credihoy_autentic_envios_v1'][self.key]['vinculo'])
        self.assertEqual(entry['estado'],'CONCILIADA_PROCESADA')
        self.assertIn(receipt.transaccion,decrypt_text(entry['reciboCifrado']))
        proof=self.journal._abrir_conciliacion(self.key,self.fingerprint,entry['journalFirma']['conciliacionCifrada'])
        self.assertEqual(proof['estadoAnterior'],'INDETERMINADA')
        audit=AuditLog.objects.get(event='credihoy_contractual_conciliacion_registrada')
        self.assertFalse(audit.payload['firmaProductivaAcreditada'])
        self.assertEqual(audit.actor,'sistema:autentic:conciliacion')
        self.assertNotIn('credihoy_formalizacion_autentic_v1',data)
        with self.assertRaisesRegex(AutenticError,'REENVIO_BLOQUEADO'):self.journal.iniciar(self.key)

    def test_unverified_or_foreign_environment_rolls_back_without_audit(self):
        with self.assertRaisesRegex(AutenticError,'EVIDENCIA_CONCILIACION_NO_VERIFICADA'):
            self.journal.conciliar(self.key,evidencia=self.evidence,verificar_evidencia=lambda _:False)
        with self.assertRaisesRegex(AutenticError,'CONCILIACION_AMBIENTE_DIFIERE'):
            self.journal.conciliar(self.key,evidencia={**self.evidence,'resultado':'NO_PROCESADA','ambiente':'PRODUCCION',
                'transaccionProveedorSha256':''},
                verificar_evidencia=lambda _:True)
        self.detail.refresh_from_db();self.assertEqual(self.detail.orchestration_data,self.original)
        self.assertFalse(AuditLog.objects.filter(event='credihoy_contractual_conciliacion_registrada').exists())
        certificate_journal=RegistroFirmasExpediente(self.case.pk,self.key,seccion='journalCertificacion')
        with self.assertRaisesRegex(AutenticError,'CONCILIACION_SOLO_JOURNAL_FIRMA'):
            certificate_journal.conciliar(self.key,evidencia=self.evidence,verificar_evidencia=lambda _:True)

    def test_parallel_reconciliation_uses_existing_row_lock_and_one_audit(self):
        evidence={**self.evidence,'resultado':'NO_PROCESADA','transaccionProveedorSha256':''};barrier=Barrier(2)
        def reconcile(_):
            try:
                journal=RegistroFirmasExpediente(self.case.pk,self.key)
                barrier.wait(timeout=10)
                return journal.conciliar(self.key,evidencia=evidence,verificar_evidencia=lambda _:True).estado
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(reconcile,range(2))),['CONCILIADA_NO_PROCESADA']*2)
        self.assertEqual(AuditLog.objects.filter(event='credihoy_contractual_conciliacion_registrada').count(),1)
        with self.assertRaisesRegex(AutenticError,'IDEMPOTENCIA_CONFLICTO'):
            self.journal.preparar(str(uuid4()),self.fingerprint)
        with self.assertRaisesRegex(AutenticError,'REENVIO_BLOQUEADO'):self.journal.iniciar(self.key)
