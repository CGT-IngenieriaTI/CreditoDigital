"""Propuestas sinteticas 9H; bloquean red/proveedor y no crean firmas."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import ModuleType
from unittest import skipUnless
from unittest.mock import patch
import uuid

from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction, DatabaseError, close_old_connections
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from django.utils import timezone

from .admin import PlanPaqueteDocumentalAdmin
from .models import AceptacionDocumento, EventoDocumental, PlanPaqueteDocumental
from .paquetes import leer_paquetes, preparar_paquete
from .tests_historial import HistorialFixture


class PaqueteFixture(HistorialFixture):
    def setUp(self):
        super().setUp()
        for code in ('add_planpaquetedocumental', 'view_planpaquetedocumental'):
            self.actor.user_permissions.add(Permission.objects.get(
                content_type__app_label='documentos', codename=code))
        self.version = self.authorize()
        self.config = {'perfil': {'producto': '390', 'modalidad': '111', 'destinacion': 'CFA'},
            'proveedor': 'AUTENTIC', 'documentos': [{'versionId': str(self.version.pk),
                'tipo': 'SOLICITUD_CREDITO', 'etapa': 'PRERADICACION', 'firmaRequerida': None,
                'orden': 1, 'precondiciones': ['CONTENIDO_DEFINITIVO', 'HOMOLOGACION_JURIDICA']}]}
        self.key = uuid.uuid4()

    def prepare(self, **overrides):
        return preparar_paquete(self.case.pk, **({'actor': self.actor, 'codigo': 'solicitud',
            'version': '1', 'configuracion': self.config, 'contexto': self.context,
            'clave_idempotencia': self.key} | overrides))


class PaqueteTests(PaqueteFixture, TestCase):
    def test_snapshot_idempotente_sin_firma_proveedor_ni_aceptacion(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('SIN_RED')):
            plan = self.prepare()
            self.assertEqual(plan.pk, self.prepare().pk)
            rows = leer_paquetes(self.case.pk, actor=self.actor)
        self.assertEqual(rows[0]['estado'], 'PROPUESTA_NO_APTA_ENVIO')
        self.assertTrue(rows[0]['versionesVigentesYContextoActual'])
        self.assertFalse(rows[0]['envioHabilitado'])
        self.assertFalse(rows[0]['firmaAcreditada'])
        self.assertEqual(rows[0]['documentos'][0]['versionSha256'], self.version.sha256)
        self.assertFalse(AceptacionDocumento.objects.exists())
        self.assertFalse(EventoDocumental.objects.exists())
        self.assertEqual(PlanPaqueteDocumental.objects.count(), 1)

    def test_conflicto_no_sobrescribe_historia(self):
        plan = self.prepare()
        with self.assertRaisesRegex(ValidationError, 'IDEMPOTENCIA_PAQUETE'):
            self.prepare(version='2')
        with self.assertRaisesRegex(ValidationError, 'VERSION_PAQUETE'):
            self.prepare(clave_idempotencia=uuid.uuid4())
        second = self.prepare(version='2', clave_idempotencia=uuid.uuid4())
        self.assertNotEqual(plan.pk, second.pk)
        self.assertEqual(PlanPaqueteDocumental.objects.count(), 2)

    def test_no_admite_pagare_ni_proveedor_linpag(self):
        config = deepcopy(self.config); config['proveedor'] = 'LINPAG'
        with self.assertRaises(ValidationError):
            self.prepare(configuracion=config)
        config = deepcopy(self.config); config['documentos'][0]['tipo'] = 'PAGARE'
        with self.assertRaises(ValidationError):
            self.prepare(configuracion=config)
        self.document.codigo = 'pagare-institucional'; self.document.save()
        # Updating the catalog invalidates the previously sealed version as well.
        with self.assertRaises(ValidationError):
            self.prepare()

    def test_paquetes_etapas_separadas_duplicados_y_tipos_estrictos(self):
        for field, value in (('orden', True), ('firmaRequerida', 'N'), ('tipo', []),
                ('precondiciones', ['APRUEBA']), ('versionId', 'incorrecto')):
            config = deepcopy(self.config); config['documentos'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.prepare(configuracion=config)
        config = deepcopy(self.config); config['documentos'].append(dict(config['documentos'][0], orden=2))
        with self.assertRaisesRegex(ValidationError, 'DUPLICADO'):
            self.prepare(configuracion=config)
        self.document.version = '2'; self.document.save()
        second = self.authorize()
        # Each version must still be current: use a second actual catalog entry.
        from .models import DocumentoLegal
        from .services import autorizar_version_documental
        doc = DocumentoLegal.objects.create(codigo='formalizacion-sintetica',
            tipo_documento='TERMINOS', titulo='Ficticio', descripcion='Ficticio', version='1')
        other = autorizar_version_documental(doc.pk, actor=self.actor, referencia='SINTETICA',
            vigente_desde=self.start, vigente_hasta=self.end)
        config['documentos'][0]['versionId'] = str(second.pk)
        config['documentos'][1] = dict(config['documentos'][1], versionId=str(other.pk), etapa='FORMALIZACION')
        with self.assertRaisesRegex(ValidationError, 'ETAPAS_DISTINTAS'):
            self.prepare(configuracion=config)

    def test_lectura_no_escribe_ni_reconstruye_vigencia(self):
        self.prepare()
        self.document.descripcion = 'Cambio posterior'; self.document.save()
        with CaptureQueriesContext(connection) as queries:
            rows = leer_paquetes(self.case.pk, actor=self.actor)
        self.assertFalse(rows[0]['versionesVigentesYContextoActual'])
        self.assertTrue(all(q['sql'].split()[0] == 'SELECT' for q in queries))
        with self.assertRaises(ValidationError):
            self.prepare(clave_idempotencia=uuid.uuid4(), version='2')

    def test_vigencia_contexto_y_auditoria_atomica(self):
        with self.assertRaises(ValidationError):
            self.prepare(ahora=timezone.now()+timedelta(days=2))
        with patch('apps.documentos.paquetes._auditar_documento', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.prepare()
        self.assertFalse(PlanPaqueteDocumental.objects.exists())
        self.prepare()
        self.case.monto_solicitado += 1; self.case.save()
        self.assertFalse(leer_paquetes(self.case.pk, actor=self.actor)[0]['versionesVigentesYContextoActual'])

    def test_permisos_objeto_en_servicio_y_admin(self):
        plan = self.prepare()
        other = get_user_model().objects.create_user('otro-plan-sintetico', is_staff=True)
        other.user_permissions.set(self.actor.user_permissions.all())
        for action in (lambda: self.prepare(actor=other), lambda: leer_paquetes(self.case.pk, actor=other)):
            with self.assertRaises(PermissionDenied):
                action()
        site = AdminSite(name='admin'); site.register(PlanPaqueteDocumental, PlanPaqueteDocumentalAdmin)
        routes = ModuleType('planes_urls'); routes.urlpatterns = [path('admin/', site.urls)]
        with override_settings(ROOT_URLCONF=routes):
            client = Client(); client.force_login(other)
            base = '/admin/documentos/planpaquetedocumental/'
            self.assertNotContains(client.get(base), self.case.numero_solicitud)
            self.assertEqual(client.get(base+str(plan.pk)+'/change/').status_code, 302)
            client.force_login(self.actor)
            self.assertEqual(client.get(base+str(plan.pk)+'/change/').status_code, 200)
            self.assertEqual(client.post(base+str(plan.pk)+'/change/', {}).status_code, 403)


@skipUnless(connection.vendor == 'postgresql', 'Requiere PostgreSQL local aislado')
class PaquetePostgresTests(PaqueteFixture, TransactionTestCase):
    def test_dos_preparaciones_concurrentes_conservan_un_plan_y_una_auditoria(self):
        from apps.utils.models import AuditLog
        from threading import Barrier
        ready = Barrier(2)

        def prepare():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.actor.pk)
                ready.wait(timeout=10)
                return self.prepare(actor=actor).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda _: prepare(), range(2)))
        self.assertEqual(a, b)
        self.assertEqual(PlanPaqueteDocumental.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(event='documento_paquete_propuesto').count(), 1)

    def test_snapshot_protegido_por_base_y_rollback_con_evidencia_bloqueado(self):
        plan = self.prepare()
        for verb in ('UPDATE documentos_planpaquetedocumental SET codigo=\'otro\'',
                'DELETE FROM documentos_planpaquetedocumental'):
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(verb+' WHERE id=%s', [plan.pk])
        with self.assertRaisesRegex(RuntimeError, 'NO_PUEDE_ELIMINAR_PLANES'):
            MigrationExecutor(connection).migrate([('documentos', '0004_versiondocumental_eventodocumental_and_more')])
        self.assertTrue(PlanPaqueteDocumental.objects.filter(pk=plan.pk).exists())
