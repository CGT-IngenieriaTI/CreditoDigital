"""Document history with synthetic cases only; no identity, signature or provider calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from threading import Barrier, Event
from types import ModuleType
from unittest import skipUnless
from unittest.mock import patch
import uuid

from django.apps import apps
from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from django.utils import timezone

from apps.documentos.admin import AceptacionDocumentoAdmin, EventoDocumentalAdmin
from apps.documentos.models import AceptacionDocumento, DocumentoLegal, EventoDocumental, VersionDocumental
from apps.documentos.services import (autorizar_version_documental, contrato_presentacion_documental,
    leer_historial_documental, presentar_documento, recibir_respuesta_documental)
from apps.solicitudes.models import Solicitud
from apps.utils.models import AuditLog


class HistorialFixture:
    def setUp(self):
        from tools.cd2b.support import seed
        self.case = seed()
        self.actor = get_user_model().objects.create_user('historial-sintetico', is_staff=True)
        self.case.asesor = self.actor
        self.case.save(update_fields=['asesor'])
        for app, code in (('solicitudes', 'view_solicitud'), ('documentos', 'add_versiondocumental'),
                ('documentos', 'add_eventodocumental'), ('documentos', 'view_eventodocumental'),
                ('documentos', 'view_aceptaciondocumento')):
            self.actor.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
        self.document = DocumentoLegal.objects.create(codigo='historial-sintetico', tipo_documento='TERMINOS',
            titulo='Documento ficticio', descripcion='Texto ficticio v1', version='1')
        self.context = {'proposito': 'PRUEBA_ADMINISTRATIVA_SIN_FIRMA', 'revisionExpediente': 'fixture-1'}
        self.start = timezone.now() - timedelta(days=1)
        self.end = timezone.now() + timedelta(days=1)

    def authorize(self, **overrides):
        return autorizar_version_documental(self.document.pk, actor=self.actor,
            **({'referencia': 'ACTA-SINTETICA-NO-INSTITUCIONAL', 'vigente_desde': self.start,
                'vigente_hasta': self.end} | overrides))

    def present(self, **overrides):
        return presentar_documento(self.case.pk, self.document.pk,
            **({'actor': self.actor, 'contexto': self.context, 'clave_idempotencia': uuid.uuid4()} | overrides))

    def respond(self, parent, **overrides):
        return recibir_respuesta_documental(self.case.pk, parent.pk,
            **({'actor': self.actor, 'contexto': self.context, 'clave_idempotencia': uuid.uuid4(),
                'sha256_presentacion': parent.sha256, 'aceptado': True} | overrides))


class HistorialDocumentalTests(HistorialFixture, TestCase):
    def test_requires_explicit_authorization_not_just_active_document(self):
        with self.assertRaisesRegex(ValidationError, 'SIN_AUTORIZACION'):
            self.present()
        for changes in ({'referencia': ''}, {'vigente_hasta': self.start}, {'vigente_desde': None},
                {'vigente_desde': self.start.replace(tzinfo=None)}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.authorize(**changes)
        self.assertFalse(VersionDocumental.objects.exists())

    def test_contract_is_bound_to_case_text_hash_context_and_authorization(self):
        version = self.authorize()
        parent = self.present()
        packet = contrato_presentacion_documental(self.case.pk, parent.pk, actor=self.actor)
        self.assertEqual(packet['documento']['descripcion'], 'Texto ficticio v1')
        self.assertEqual(packet['documentoSha256'], version.evidencia['documentoSha256'])
        self.assertEqual(packet['solicitudId'], str(self.case.pk))
        self.assertEqual(packet['contexto']['declarado'], self.context)
        for field in ('aceptacionTitularAutenticada', 'firmaContratoAcreditada', 'recepcionRealHabilitada'):
            self.assertFalse(packet[field])
        self.assertFalse(AceptacionDocumento.objects.exists())

    def test_multiple_versions_and_responses_preserve_legacy_acceptance_unchanged(self):
        legacy = AceptacionDocumento.objects.create(solicitud=self.case, documento=self.document,
            aceptado=True, fecha_aceptacion=self.start)
        before = AceptacionDocumento.objects.values().get(pk=legacy.pk)
        self.authorize(); first = self.present(); self.respond(first)
        self.document.version = '2'; self.document.descripcion = 'Texto ficticio v2'; self.document.save()
        self.authorize(); second = self.present(); self.respond(second, aceptado=False)
        history = leer_historial_documental(self.case.pk, actor=self.actor)
        self.assertEqual(len(history['eventos']), 4)
        self.assertEqual({e['version'] for e in history['eventos']}, {'1', '2'})
        self.assertTrue(all(e['integridad'] == 'VERIFICADA' for e in history['eventos']))
        self.assertEqual(AceptacionDocumento.objects.values().get(pk=legacy.pk), before)
        self.assertEqual(history['historico']['aceptaciones'][0]['integridadVersion'], 'LEGADO_SIN_SNAPSHOT')
        self.assertFalse(history['aceptacionTitularAutenticada'])
        self.assertFalse(history['firmaContratoAcreditada'])

    def test_presentation_and_reception_are_idempotent_without_duplicate_audit(self):
        version = self.authorize(); self.assertEqual(self.authorize().pk, version.pk)
        key = uuid.uuid4(); parent = self.present(clave_idempotencia=key)
        audit_count = AuditLog.objects.count()
        self.assertEqual(self.present(clave_idempotencia=key).pk, parent.pk)
        self.assertEqual(AuditLog.objects.count(), audit_count)
        key = uuid.uuid4(); response = self.respond(parent, clave_idempotencia=key)
        audit_count = AuditLog.objects.count()
        self.assertEqual(self.respond(parent, clave_idempotencia=key).pk, response.pk)
        self.assertEqual(AuditLog.objects.count(), audit_count)
        self.assertEqual(EventoDocumental.objects.count(), 2)

    def test_same_key_with_changed_parameters_is_conflict_not_a_new_response(self):
        self.authorize(); parent = self.present()
        key = uuid.uuid4(); self.respond(parent, clave_idempotencia=key)
        with self.assertRaisesRegex(ValidationError, 'IDEMPOTENCIA'):
            self.respond(parent, clave_idempotencia=key, aceptado=False)
        with self.assertRaisesRegex(ValidationError, 'YA_TIENE_RESPUESTA'):
            self.respond(parent, aceptado=False)
        with self.assertRaisesRegex(ValidationError, 'IDEMPOTENCIA'):
            self.respond(parent, clave_idempotencia=parent.clave_idempotencia)

    def test_changes_of_content_version_active_status_and_expiration_fail_closed(self):
        self.authorize(); parent = self.present()
        for change in ({'descripcion': 'Texto alterado'}, {'version': '2'}, {'activo': False}):
            old = {key: getattr(self.document, key) for key in change}
            for key, val in change.items():
                setattr(self.document, key, val)
            self.document.save()
            with self.subTest(change=change), self.assertRaisesRegex(ValidationError, 'NO_VIGENTE_O_MODIFICADA'):
                self.respond(parent)
            for key, val in old.items():
                setattr(self.document, key, val)
            self.document.save()
        from unittest.mock import patch
        with patch('django.utils.timezone.now', return_value=self.end + timedelta(seconds=1)):
            with self.assertRaisesRegex(ValidationError, 'NO_VIGENTE_O_MODIFICADA'):
                self.respond(parent)
        self.assertEqual(EventoDocumental.objects.count(), 1)

    def test_context_case_changes_wrong_hash_and_non_bool_are_rejected(self):
        self.authorize(); parent = self.present()
        for change in ({'contexto': {'proposito': 'OTRO'}}, {'sha256_presentacion': '0'*64},
                {'aceptado': 'true'}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.respond(parent, **change)
        self.case.monto_solicitado += 1; self.case.save(update_fields=['monto_solicitado'])
        with self.assertRaisesRegex(ValidationError, 'CONTEXTO_NO_COINCIDE'):
            self.respond(parent)
        self.assertEqual(EventoDocumental.objects.count(), 1)

    def test_context_and_idempotency_key_are_validated(self):
        self.authorize()
        for context in ({}, None, {'proposito': ''}, {'proposito': 'X', 'cedula': 'NO_PERMITIDO'}):
            with self.subTest(context=context), self.assertRaises(ValidationError):
                self.present(contexto=context)
        with self.assertRaisesRegex(ValidationError, 'CLAVE_DOCUMENTAL_INVALIDA'):
            self.present(clave_idempotencia='bad-key')

    def test_append_only_orm_and_protected_relations(self):
        version = self.authorize(); parent = self.present(); self.respond(parent)
        for row in (version, parent):
            with self.assertRaisesRegex(ValidationError, 'INMUTABLE'):
                row.save()
            with self.assertRaises(ValidationError):
                row.delete()
            with self.assertRaises(ValidationError):
                type(row).objects.filter(pk=row.pk).update(sha256='0'*64)
            with self.assertRaises(ValidationError):
                type(row).objects.filter(pk=row.pk).delete()
        for row in (self.document, self.case, self.actor):
            with self.assertRaises(ProtectedError):
                row.delete()

    def test_new_content_cannot_reuse_a_sealed_version_number(self):
        self.authorize(); self.document.descripcion = 'Alteracion'; self.document.save()
        with self.assertRaisesRegex(ValidationError, 'YA_SELLADA'):
            self.authorize()
        with self.assertRaisesRegex(ValidationError, 'NO_VIGENTE_O_MODIFICADA'):
            self.present()

    def test_other_owner_and_case_do_not_grant_history_or_response_access(self):
        self.authorize(); parent = self.present()
        other = get_user_model().objects.create_user('otro-documental', is_staff=True)
        other.user_permissions.set(self.actor.user_permissions.all())
        for action in (lambda: self.present(actor=other), lambda: self.respond(parent, actor=other),
                lambda: leer_historial_documental(self.case.pk, actor=other),
                lambda: contrato_presentacion_documental(self.case.pk, parent.pk, actor=other)):
            with self.assertRaises(PermissionDenied): action()
        second = Solicitud.objects.create(solicitante=self.case.solicitante, asesor=self.actor)
        with self.assertRaises(PermissionDenied):
            recibir_respuesta_documental(second.pk, parent.pk, actor=self.actor, contexto=self.context,
                clave_idempotencia=uuid.uuid4(), sha256_presentacion=parent.sha256, aceptado=True)

    def test_permissions_are_required_not_only_case_assignment(self):
        self.actor.user_permissions.clear()
        for action in (self.authorize, self.present,
                lambda: leer_historial_documental(self.case.pk, actor=self.actor)):
            with self.assertRaises(PermissionDenied): action()

    def test_reads_are_select_only_and_recover_original_text_after_current_edit(self):
        self.authorize(); parent = self.present(); self.respond(parent, aceptado=False)
        self.document.version = '2'; self.document.descripcion = 'Modificado'; self.document.save()
        with CaptureQueriesContext(connection) as queries:
            packet = contrato_presentacion_documental(self.case.pk, parent.pk, actor=self.actor)
            history = leer_historial_documental(self.case.pk, actor=self.actor)
        self.assertEqual(packet['documento']['version'], '1')
        self.assertEqual(packet['documento']['descripcion'], 'Texto ficticio v1')
        self.assertEqual(history['eventos'][-1]['tipo'], 'NEGATIVA_ADMIN')
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))

    def test_same_timestamp_keeps_presentation_before_response_regardless_of_uuid(self):
        self.authorize()
        key1, key2 = uuid.uuid4(), uuid.uuid4()
        parent_id, response_id = uuid.UUID(int=2), uuid.UUID(int=1)
        now = timezone.now()
        with patch('django.utils.timezone.now', return_value=now), patch('uuid.uuid4', side_effect=[parent_id, response_id]):
            parent = presentar_documento(self.case.pk, self.document.pk, actor=self.actor,
                contexto=self.context, clave_idempotencia=key1)
            response = recibir_respuesta_documental(self.case.pk, parent.pk, actor=self.actor,
                contexto=self.context, clave_idempotencia=key2, sha256_presentacion=parent.sha256, aceptado=False)
        self.assertEqual(parent.created_at, response.created_at)
        with CaptureQueriesContext(connection) as queries:
            history = leer_historial_documental(self.case.pk, actor=self.actor)
        self.assertEqual([row['eventoId'] for row in history['eventos']], [str(parent_id), str(response_id)])
        self.assertTrue(all(row['integridad'] == 'VERIFICADA' for row in history['eventos']))
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))

    def test_admin_history_is_read_only_scoped_and_does_not_leak_other_cases(self):
        self.authorize(); parent = self.present(); self.respond(parent)
        legacy = AceptacionDocumento.objects.create(solicitud=self.case, documento=self.document,
            aceptado=True, fecha_aceptacion=self.start)
        other = get_user_model().objects.create_user('otro-admin-documental', is_staff=True)
        other.user_permissions.set(self.actor.user_permissions.all())
        site = AdminSite(name='documental_test')
        site.register(EventoDocumental, EventoDocumentalAdmin)
        site.register(AceptacionDocumento, AceptacionDocumentoAdmin)
        routes = ModuleType('documentos_history_urls'); routes.urlpatterns = [path('admin/', site.urls)]
        client = Client(); client.force_login(self.actor)
        with override_settings(ROOT_URLCONF=routes), CaptureQueriesContext(connection) as queries:
            for model, identifier in (('eventodocumental', parent.pk), ('aceptaciondocumento', legacy.pk)):
                base = '/admin/documentos/' + model + '/'
                self.assertEqual(client.get(base).status_code, 200)
                response = client.get(base + str(identifier) + '/change/')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('name="_save"', response.content.decode())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        with override_settings(ROOT_URLCONF=routes):
            self.assertEqual(client.post('/admin/documentos/eventodocumental/' + str(parent.pk) + '/change/', {}).status_code, 403)
            client.force_login(other)
            listing = client.get('/admin/documentos/eventodocumental/')
            self.assertNotIn(self.case.numero_solicitud, listing.content.decode())
            for suffix in ('change/', 'history/', 'delete/'):
                response = client.get('/admin/documentos/eventodocumental/' + str(parent.pk) + '/' + suffix)
                self.assertIn(response.status_code, (302, 403, 404))

    def test_atomic_audit_failure_does_not_leave_partial_presentation(self):
        from unittest.mock import patch
        self.authorize()
        with patch('apps.documentos.services._auditar_documento', side_effect=RuntimeError('SYNTHETIC')):
            with self.assertRaises(RuntimeError): self.present()
        self.assertFalse(EventoDocumental.objects.exists())

    def test_in_memory_tampering_does_not_accredit_evidence_or_identity(self):
        from unittest.mock import patch
        from apps.documentos.services import _evento_integro, _version_integra
        self.authorize(); parent = self.present()
        parent.evidencia = dict(parent.evidencia, firmaContratoAcreditada=True)
        self.assertFalse(_evento_integro(parent))
        version = parent.version_documental
        version.evidencia = dict(version.evidencia, documentoSha256='0'*64)
        self.assertFalse(_version_integra(version))
        with patch('apps.documentos.services.EventoDocumental.objects') as manager:
            manager.select_related.return_value.get.return_value = parent
            with self.assertRaisesRegex(ValidationError, 'INCONSISTENTE'):
                contrato_presentacion_documental(self.case.pk, parent.pk, actor=self.actor)

    def test_legacy_upsert_still_works_without_touching_new_history(self):
        self.authorize(); parent = self.present(); self.respond(parent)
        row, _ = AceptacionDocumento.objects.update_or_create(solicitud=self.case,
            documento=self.document, defaults={'aceptado': False, 'fecha_aceptacion': self.start})
        again, created = AceptacionDocumento.objects.update_or_create(solicitud=self.case,
            documento=self.document, defaults={'aceptado': True, 'fecha_aceptacion': self.start})
        self.assertFalse(created); self.assertEqual(row.pk, again.pk)
        self.assertIsNone(again.evidencia_version)
        self.assertEqual(EventoDocumental.objects.count(), 2)

    def test_reverse_migration_guard_preserves_new_evidence(self):
        from types import SimpleNamespace
        migration = import_module('apps.documentos.migrations.0004_versiondocumental_eventodocumental_and_more')
        editor = SimpleNamespace(connection=connection)
        migration.proteger_historial_al_revertir(apps, editor)
        self.authorize()
        with self.assertRaisesRegex(RuntimeError, 'NO_PUEDE_ELIMINAR_HISTORIAL'):
            migration.proteger_historial_al_revertir(apps, editor)


@skipUnless(connection.vendor == 'postgresql', 'Requires isolated PostgreSQL, not SQLite claims')
class PostgreSQLHistorialTests(HistorialFixture, TransactionTestCase):
    def _parallel(self, tasks):
        barrier = Barrier(len(tasks))

        def run(task):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                barrier.wait(timeout=10)
                try:
                    return str(task(actor).pk)
                except ValidationError:
                    return 'CONFLICTO_ESPERADO'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = [pool.submit(run, task) for task in tasks]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_identical_presentations_create_one_event_and_one_audit(self):
        self.authorize(); key = uuid.uuid4()
        before = AuditLog.objects.count()
        result = self._parallel([lambda actor: self.present(actor=actor, clave_idempotencia=key)] * 2)
        self.assertEqual(result[0], result[1])
        self.assertEqual(EventoDocumental.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), before + 1)

    def test_concurrent_authorization_seals_one_version(self):
        def authorize(actor):
            return autorizar_version_documental(self.document.pk, actor=actor,
                referencia='ACTA-SINTETICA', vigente_desde=self.start, vigente_hasta=self.end)
        result = self._parallel([authorize, authorize])
        self.assertEqual(result[0], result[1])
        self.assertEqual(VersionDocumental.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(event='documento_version_autorizada').count(), 1)

    def test_concurrent_same_response_key_is_idempotent(self):
        self.authorize(); parent = self.present(); key = uuid.uuid4()
        result = self._parallel([lambda actor: self.respond(parent, actor=actor, clave_idempotencia=key)] * 2)
        self.assertEqual(result[0], result[1])
        self.assertEqual(parent.respuestas.count(), 1)

    def test_concurrent_opposite_responses_cannot_overwrite_or_duplicate(self):
        self.authorize(); parent = self.present()
        result = self._parallel([lambda actor: self.respond(parent, actor=actor),
            lambda actor: self.respond(parent, actor=actor, aceptado=False)])
        self.assertEqual(result.count('CONFLICTO_ESPERADO'), 1)
        self.assertEqual(parent.respuestas.count(), 1)

    def test_document_update_locked_before_response_is_rejected_after_commit(self):
        self.authorize(); parent = self.present(); started = Event()

        def receive():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                started.set()
                with self.assertRaisesRegex(ValidationError, 'NO_VIGENTE_O_MODIFICADA'):
                    self.respond(parent, actor=actor)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                document = DocumentoLegal.objects.select_for_update().get(pk=self.document.pk)
                future = pool.submit(receive)
                self.assertTrue(started.wait(10))
                document.descripcion = 'Cambio concurrente'; document.save()
            future.result(timeout=20)
        self.assertFalse(parent.respuestas.exists())

    def test_database_triggers_reject_raw_updates_and_deletes(self):
        self.authorize(); parent = self.present()
        for table in ('documentos_versiondocumental', 'documentos_eventodocumental'):
            for sql in (f'UPDATE {table} SET sha256 = sha256', f'DELETE FROM {table}'):
                with self.subTest(sql=sql), self.assertRaisesRegex(DatabaseError, 'INMUTABLE'):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(sql)
        self.assertTrue(EventoDocumental.objects.filter(pk=parent.pk).exists())

    def test_database_constraints_reject_replay_and_foreign_parent(self):
        self.authorize(); parent = self.present()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventoDocumental.objects.create(solicitud=self.case, version_documental=parent.version_documental,
                tipo='PRESENTACION', actor=self.actor, clave_idempotencia=parent.clave_idempotencia,
                evidencia={}, sha256='0'*64)
        other = Solicitud.objects.create(solicitante=self.case.solicitante, asesor=self.actor)
        with self.assertRaisesRegex(IntegrityError, 'NO_VINCULADA'), transaction.atomic():
            EventoDocumental.objects.create(solicitud=other, version_documental=parent.version_documental,
                tipo='ACEPTACION_ADMIN', presentacion=parent, actor=self.actor,
                clave_idempotencia=uuid.uuid4(), evidencia={}, sha256='0'*64)

    def test_migrations_upgrade_legacy_rows_and_refuse_destructive_downgrade(self):
        latest = ('documentos', '0005_planpaquetedocumental')
        old = ('documentos', '0002_aceptaciondocumento_llego_al_final_and_more')
        try:
            executor = MigrationExecutor(connection); executor.migrate([old])
            old_apps = executor.loader.project_state([old]).apps
            row = old_apps.get_model('documentos', 'AceptacionDocumento').objects.create(
                solicitud_id=self.case.pk, documento_id=self.document.pk, aceptado=False,
                fecha_aceptacion=self.start, llego_al_final=True, user_agent='HISTORICO_SINTETICO')
            executor = MigrationExecutor(connection); executor.migrate([latest])
            restored = AceptacionDocumento.objects.get(pk=row.pk)
            self.assertIsNone(restored.evidencia_version)
            self.assertFalse(restored.aceptado)
            self.assertEqual(restored.fecha_aceptacion, self.start)
            self.assertEqual(restored.user_agent, 'HISTORICO_SINTETICO')
            self.authorize(); parent = self.present(); self.respond(parent)
            with self.assertRaisesRegex(RuntimeError, 'NO_PUEDE_ELIMINAR_HISTORIAL'):
                MigrationExecutor(connection).migrate([('documentos', '0003_aceptaciondocumento_evidencia_version')])
            self.assertEqual(EventoDocumental.objects.count(), 2)
            self.assertTrue(AceptacionDocumento.objects.filter(pk=row.pk).exists())
        finally:
            MigrationExecutor(connection).migrate([latest])

    def test_empty_additive_migration_can_be_reversed_and_reapplied(self):
        latest = ('documentos', '0005_planpaquetedocumental')
        previous = ('documentos', '0003_aceptaciondocumento_evidencia_version')
        try:
            MigrationExecutor(connection).migrate([previous])
            self.assertNotIn('documentos_eventodocumental', connection.introspection.table_names())
        finally:
            MigrationExecutor(connection).migrate([latest])
        self.authorize(); self.assertTrue(self.present().pk)
