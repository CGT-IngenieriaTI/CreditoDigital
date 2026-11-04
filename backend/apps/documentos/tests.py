from django.test import TestCase
from django.utils import timezone
from django.db import connection
from django.test.utils import CaptureQueriesContext
from datetime import timedelta

from apps.documentos.models import DocumentoLegal, TipoDocumento
from apps.documentos.services import get_active_documents
from apps.documentos.services import leer_evidencia_documental


class EvidenciaDocumentalTests(TestCase):
    def setUp(self):
        from tools.cd2b.support import seed
        self.s = seed()
        self.now = timezone.now()

    def test_empty_inventory_does_not_create_default_documents_or_write(self):
        with CaptureQueriesContext(connection) as queries:
            packet = leer_evidencia_documental(self.s.pk, ahora=self.now)
        self.assertEqual(packet['aceptaciones'], [])
        self.assertFalse(packet['firmaContratoAcreditada'])
        self.assertEqual(DocumentoLegal.objects.count(), 0)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))

    def test_registered_legal_acceptance_never_claims_accepted_version_or_contract_signature(self):
        from apps.documentos.models import AceptacionDocumento
        document = DocumentoLegal.objects.create(codigo='legal-synthetic', tipo_documento=TipoDocumento.CENTRALES,
            titulo='SYNTHETIC', descripcion='SYNTHETIC', version='1')
        AceptacionDocumento.objects.create(solicitud=self.s, documento=document, aceptado=True,
            fecha_aceptacion=self.now, llego_al_final=True, ip_address='127.0.0.1', user_agent='PRIVATE')
        document.version='2'; document.save(update_fields=['version'])
        packet=leer_evidencia_documental(self.s.pk, ahora=self.now)
        row=packet['aceptaciones'][0]
        self.assertEqual(row['estado'], 'REGISTRADA')
        self.assertEqual(row['versionActualDocumento'], '2')
        self.assertIsNone(row['versionAceptada'])
        self.assertFalse(packet['versionAceptadaDisponible'])
        self.assertFalse(packet['firmaContratoAcreditada'])
        self.assertNotIn('PRIVATE', str(packet)); self.assertNotIn('127.0.0.1', str(packet))

    def test_future_declined_inactive_and_other_case_acceptances_are_not_mixed(self):
        from apps.documentos.models import AceptacionDocumento
        from apps.solicitudes.models import Solicitud
        other=Solicitud.objects.create(solicitante=self.s.solicitante)
        for index, (case, accepted, date) in enumerate(((self.s, True, self.now+timedelta(seconds=1)),
                (self.s, False, self.now), (other, True, self.now))):
            document=DocumentoLegal.objects.create(codigo='doc-'+str(index), tipo_documento=TipoDocumento.DATOS,
                titulo='SYNTHETIC', descripcion='SYNTHETIC', activo=False)
            AceptacionDocumento.objects.create(solicitud=case, documento=document,
                aceptado=accepted, fecha_aceptacion=date)
        rows=leer_evidencia_documental(self.s.pk, ahora=self.now)['aceptaciones']
        self.assertEqual(len(rows),2)
        self.assertEqual({r['estado'] for r in rows}, {'FECHA_NO_VALIDA','NO_ACEPTADA'})
        self.assertTrue(all(not r['documentoActivo'] for r in rows))
        with self.assertRaisesRegex(ValueError,'SIN_ZONA'):
            leer_evidencia_documental(self.s.pk, ahora=self.now.replace(tzinfo=None))


class DocumentoLegalServiceTests(TestCase):
    def test_catalog_filters_centrales_without_deactivating_other_documents(self):
        DocumentoLegal.objects.create(
            codigo="tratamiento-datos",
            tipo_documento=TipoDocumento.DATOS,
            titulo="Tratamiento",
            descripcion="Documento antiguo",
            orden=4,
            activo=True,
        )
        DocumentoLegal.objects.create(
            codigo="terminos-condiciones",
            tipo_documento=TipoDocumento.TERMINOS,
            titulo="Terminos",
            descripcion="Documento antiguo",
            orden=5,
            activo=True,
        )

        documents = list(get_active_documents())

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].codigo, "centrales-riesgo")
        self.assertTrue(DocumentoLegal.objects.get(codigo="tratamiento-datos").activo)
        self.assertTrue(DocumentoLegal.objects.get(codigo="terminos-condiciones").activo)

    def test_existing_legal_content_version_and_activation_are_never_rewritten(self):
        from .services import preparar_version_documental
        document = DocumentoLegal.objects.create(codigo='centrales-riesgo',
            tipo_documento=TipoDocumento.CENTRALES, titulo='Texto autorizado',
            descripcion='Contenido institucional conservado', version='7', orden=9, activo=False)
        before = preparar_version_documental(document)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(list(get_active_documents()), [])
        document.refresh_from_db()
        self.assertEqual(preparar_version_documental(document), before)
        self.assertFalse(document.activo)
        self.assertEqual(document.orden, 9)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))

    def test_existing_active_document_is_returned_without_writes(self):
        document = DocumentoLegal.objects.create(codigo='centrales-riesgo',
            tipo_documento=TipoDocumento.CENTRALES, titulo='Autorizado', descripcion='Conservado')
        with CaptureQueriesContext(connection) as queries:
            for _ in range(2):
                self.assertEqual([row.pk for row in get_active_documents()], [document.pk])
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))


class VersionDocumentalTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from tools.cd2b.support import seed
        from apps.documentos.services import preparar_version_documental
        self.s=seed();self.now=timezone.now()
        self.user=get_user_model().objects.create_user('documentos-synthetic',is_staff=True)
        self.s.asesor=self.user;self.s.save(update_fields=['asesor'])
        for app,code in (('solicitudes','view_solicitud'),('documentos','add_aceptaciondocumento')):
            self.user.user_permissions.add(Permission.objects.get(content_type__app_label=app,codename=code))
        self.document=DocumentoLegal.objects.create(codigo='version-texto-sintetico',
            tipo_documento=TipoDocumento.TERMINOS,titulo='TEXTO SINTETICO',descripcion='Contenido version 1',version='1')
        self.shown=preparar_version_documental(self.document)

    def register(self, **kwargs):
        from apps.documentos.services import registrar_aceptacion_versionada
        data={'actor':self.user,'version_presentada':'1','sha256_presentado':self.shown['sha256'],
            'aceptado':True,'ahora':self.now}
        return registrar_aceptacion_versionada(self.s.pk,self.document.pk,**(data|kwargs))

    def test_snapshot_preserves_presented_version_after_edits_and_retry_is_idempotent(self):
        from apps.utils.models import AuditLog
        row=self.register();audit=AuditLog.objects.count()
        self.assertEqual(row.pk,self.register().pk)
        self.assertEqual(AuditLog.objects.count(),audit)
        self.document.version='2';self.document.descripcion='TEXTO DIFERENTE';self.document.save()
        with CaptureQueriesContext(connection) as queries:
            packet=leer_evidencia_documental(self.s.pk,ahora=timezone.now())
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        result=packet['aceptaciones'][0]
        self.assertEqual((result['versionAceptada'],result['versionActualDocumento']),('1','2'))
        self.assertEqual(result['integridadVersion'],'VERIFICADA')
        self.assertEqual(result['alcanceVersion'],'TEXTO_DOCUMENTOLEGAL_NO_PDF')
        self.assertTrue(packet['versionAceptadaDisponible'])
        self.assertFalse(packet['aceptacionTitularAutenticada'])
        self.assertFalse(packet['firmaContratoAcreditada'])
        self.assertNotIn('Contenido version 1',str(packet))

    def test_stale_version_content_or_future_time_never_creates_acceptance(self):
        from django.core.exceptions import ValidationError
        from apps.documentos.models import AceptacionDocumento
        for change in ({'version_presentada':'2'},{'sha256_presentado':'a'*64},
                {'ahora':timezone.now()+timedelta(days=1)},{'aceptado':'true'}):
            with self.subTest(change=change),self.assertRaises(ValidationError):
                self.register(**change)
        self.assertFalse(AceptacionDocumento.objects.exists())

    def test_legacy_acceptance_is_not_reconstructed_from_current_document(self):
        from django.core.exceptions import ValidationError
        from apps.documentos.models import AceptacionDocumento
        row=AceptacionDocumento.objects.create(solicitud=self.s,documento=self.document,
            aceptado=True,fecha_aceptacion=self.now)
        with self.assertRaisesRegex(ValidationError,'NO_RECONSTRUIR'):
            self.register()
        row.evidencia_version={'version':'INVENTADA'}
        with self.assertRaisesRegex(ValidationError,'HISTORICA_NO_RECONSTRUIR'):
            row.save()
        row.refresh_from_db();self.assertIsNone(row.evidencia_version)

    def test_snapshot_integrity_and_case_binding_fail_closed(self):
        from apps.documentos.models import AceptacionDocumento
        row=self.register()
        evidence=dict(row.evidencia_version);evidence['solicitudId']='OTRO_EXPEDIENTE'
        AceptacionDocumento.objects.filter(pk=row.pk).update(evidencia_version=evidence)
        packet=leer_evidencia_documental(self.s.pk,ahora=timezone.now())
        self.assertIsNone(packet['aceptaciones'][0]['versionAceptada'])
        self.assertEqual(packet['aceptaciones'][0]['integridadVersion'],'NO_ACREDITADA')
        self.assertFalse(packet['firmaContratoAcreditada'])

    def test_other_operator_or_missing_permission_cannot_register_acceptance(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission
        from django.core.exceptions import PermissionDenied
        other=get_user_model().objects.create_user('other-documentos-synthetic',is_staff=True)
        for app,code in (('solicitudes','view_solicitud'),('documentos','add_aceptaciondocumento')):
            other.user_permissions.add(Permission.objects.get(content_type__app_label=app,codename=code))
        with self.assertRaises(PermissionDenied):self.register(actor=other)
        self.user.user_permissions.clear()
        for field in ('_perm_cache','_user_perm_cache','_group_perm_cache'):
            self.user.__dict__.pop(field,None)
        with self.assertRaises(PermissionDenied):self.register()

    def test_decline_and_snapshot_mutation_do_not_become_accepted_or_signed(self):
        from django.core.exceptions import ValidationError
        row=self.register(aceptado=False)
        self.assertEqual(row.pk,self.register(aceptado=False).pk)
        packet=leer_evidencia_documental(self.s.pk,ahora=timezone.now())
        self.assertEqual(packet['aceptaciones'][0]['estado'],'NO_ACEPTADA')
        self.assertIsNone(packet['aceptaciones'][0]['versionAceptada'])
        self.assertEqual(packet['aceptaciones'][0]['versionPresentadaVerificada'],'1')
        row.aceptado=True
        with self.assertRaisesRegex(ValidationError,'VERSIONADA_NO_SOBRESCRIBIR'):row.save()
        self.assertFalse(packet['firmaContratoAcreditada'])

    def test_migration_reversal_cannot_drop_registered_version_evidence(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        migration=import_module('apps.documentos.migrations.0003_aceptaciondocumento_evidencia_version')
        editor=SimpleNamespace(connection=SimpleNamespace(alias='default'))
        migration.proteger_evidencia_al_revertir(apps,editor)
        self.register()
        with self.assertRaisesRegex(RuntimeError,'NO_PUEDE_ELIMINAR_EVIDENCIA'):
            migration.proteger_evidencia_al_revertir(apps,editor)
