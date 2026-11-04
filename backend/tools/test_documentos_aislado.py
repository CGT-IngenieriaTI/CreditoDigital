"""Reproducible 9G/9H tests. Never imports project settings or institutional secrets.

Run with backend/venv Python: -B tools/test_documentos_aislado.py [--postgres] [--check] [--demo]
PostgreSQL mode owns and removes only its randomly named local container/network.
"""
import argparse
import base64
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ.pop('DJANGO_SETTINGS_MODULE', None)


_DOCKER_LOCAL_ENDPOINT=None


def docker_local_command(*args):
    global _DOCKER_LOCAL_ENDPOINT
    if _DOCKER_LOCAL_ENDPOINT is None:
        if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_TLS_VERIFY'):
            raise RuntimeError('DOCKER_OVERRIDE_NO_PERMITIDO_EN_PRUEBA_LOCAL')
        context=subprocess.run(['docker','context','show'],check=True,capture_output=True,text=True,timeout=30).stdout.strip()
        info=json.loads(subprocess.run(['docker','context','inspect',context],check=True,
            capture_output=True,text=True,timeout=30).stdout)
        endpoint=info[0]['Endpoints']['docker']['Host']
        if (endpoint not in {'npipe:////./pipe/dockerDesktopLinuxEngine','npipe:////./pipe/docker_engine'}
                and not endpoint.startswith('unix:///')):
            raise RuntimeError('DAEMON_DOCKER_REMOTO_PROHIBIDO')
        _DOCKER_LOCAL_ENDPOINT=endpoint
    return ['docker','--host',_DOCKER_LOCAL_ENDPOINT,*args]


def docker(*args, **kwargs):
    return subprocess.run(docker_local_command(*args), check=True, capture_output=True, text=True,
        timeout=120, **kwargs).stdout.strip()


@contextmanager
def postgres_efimero():
    name = 'credihoy-homologacion-' + uuid.uuid4().hex
    password = secrets.token_hex(32)
    network_created = container_created = False
    try:
        # An already installed image, not a production container or volume.
        docker('image', 'inspect', 'postgres:16-alpine', '--format', '{{.Id}}')
        docker('network', 'create', '--opt', 'com.docker.network.bridge.enable_icc=false',
            '--label', 'purpose=credihoy-9gh-tests', name)
        network_created = True
        env = dict(os.environ, POSTGRES_PASSWORD=password)
        docker('run', '--pull=never', '-d', '--name', name, '--network', name,
            '--label', 'purpose=credihoy-9gh-tests', '--memory', '768m', '--cpus', '2',
            '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,size=512m',
            '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD', 'postgres:16-alpine', env=env)
        container_created = True
        for _ in range(60):
            # The image's bootstrap postmaster is socket-only; wait for final TCP.
            ready = subprocess.run(docker_local_command('exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'),
                capture_output=True, timeout=10)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('POSTGRES_LOCAL_NO_DISPONIBLE')
        sql = ("CREATE ROLE credihoy_homologacion LOGIN NOSUPERUSER NOCREATEROLE CREATEDB PASSWORD '"
            + password + "';\nCREATE DATABASE credihoy_homologacion OWNER credihoy_homologacion;")
        docker('exec', '-i', name, 'psql', '-U', 'postgres', '-v', 'ON_ERROR_STOP=1', input=sql)
        port = int(docker('port', name, '5432/tcp').rsplit(':', 1)[1])
        yield {'ENGINE': 'django.db.backends.postgresql', 'HOST': '127.0.0.1', 'PORT': port,
            'NAME': 'credihoy_homologacion', 'USER': 'credihoy_homologacion', 'PASSWORD': password,
            'CONN_MAX_AGE': 0, 'OPTIONS': {'connect_timeout': 5}}, name
    finally:
        if container_created:
            docker('rm', '-f', name)
        if network_created:
            docker('network', 'rm', name)


def configurar(database,draft_only=False,personal=False):
    from django.conf import settings
    settings.configure(BASE_DIR=ROOT, SECRET_KEY='synthetic-local-documentary-tests-only', DEBUG=False,
        USE_TZ=True, TIME_ZONE='America/Bogota', DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
        ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'],
        ROOT_URLCONF='core.urls_credihoy_titular' if draft_only else 'core.urls_credihoy_publico' if personal else 'core.urls_firma',
        INSTALLED_APPS=(['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions',
            'rest_framework','apps.utils','apps.usuarios','apps.solicitudes','apps.xcore_consumo'] if draft_only else
            ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
            'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
            'rest_framework', 'apps.utils', 'apps.usuarios', 'apps.solicitudes', 'apps.documentos',
            'apps.preselecta', 'apps.historial_pago', 'apps.xcore', 'apps.decisiones',
            'apps.xcore_consumo', 'apps.integraciones.risk_gateway.operaciones']),
        DATABASES={'default': database, **({'historico':{**database,'NAME':'credihoy_historico_sintetico'}}
            if database['ENGINE'].endswith('postgresql') else {})},
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        MIDDLEWARE=['django.contrib.sessions.middleware.SessionMiddleware',
            'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
            'django.contrib.messages.middleware.MessageMiddleware'],
        TEMPLATES=[{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True,
            'OPTIONS': {'context_processors': ['django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}],
        STATIC_URL='/static/', PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
        SESSION_COOKIE_NAME='credihoy_9gh_synthetic_session', CSRF_COOKIE_NAME='credihoy_9gh_synthetic_csrf',
        EMAIL_BACKEND='django.core.mail.backends.dummy.EmailBackend',
        REST_FRAMEWORK={'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
            'DEFAULT_PARSER_CLASSES': ['rest_framework.parsers.JSONParser']},
        XCORE_CONSUMO_ORACLE_ENABLED=False, RISK_GATEWAY_ENABLED='N', CREDIT_PIPELINE_ASYNC=False,
        CREDIHOY_FUNCIONAL_ENABLED=True, CREDIHOY_CONSENTIMIENTO_REAL_ENABLED=True,
        OTP_AES_KEY_B64=base64.b64encode(b'x' * 32).decode('ascii'))


def demo(port):
    from datetime import timedelta
    from types import ModuleType
    from django.conf import settings
    from django.contrib.admin import AdminSite
    from django.contrib.auth.models import Permission
    from django.core.management import call_command
    from django.core.servers.basehttp import run
    from django.core.wsgi import get_wsgi_application
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.urls import path
    from django.utils import timezone
    from apps.documentos.admin import AceptacionDocumentoAdmin, EventoDocumentalAdmin, VersionDocumentalAdmin
    from apps.documentos.models import AceptacionDocumento, DocumentoLegal, EventoDocumental, VersionDocumental
    from apps.documentos.services import autorizar_version_documental, presentar_documento, recibir_respuesta_documental
    from apps.solicitudes.models import Solicitud
    from apps.solicitudes.admin import SolicitudAdmin
    from tools.test_credihoy_bandeja import BandejaTests
    call_command('migrate', verbosity=0, interactive=False)
    fixture = BandejaTests(); fixture.setUp()
    actor = fixture.user
    for code in ('add_versiondocumental', 'view_versiondocumental', 'add_eventodocumental',
            'view_eventodocumental', 'view_aceptaciondocumento'):
        actor.user_permissions.add(Permission.objects.get(content_type__app_label='documentos', codename=code))
    document = DocumentoLegal.objects.create(codigo='demo-sintetica', tipo_documento='TERMINOS',
        titulo='Documento ficticio - NO ES UN TEXTO JURIDICO', descripcion='Texto sintetico version 1', version='1')
    now = timezone.now()
    AceptacionDocumento.objects.create(solicitud=fixture.s, documento=document,
        aceptado=False, fecha_aceptacion=now-timedelta(days=1))
    for number, answer in ((1, True), (2, False)):
        document.version = str(number); document.descripcion = 'Texto sintetico version ' + str(number)
        document.save()
        autorizar_version_documental(document.pk, actor=actor, referencia='ACTA-FICTICIA-NO-INSTITUCIONAL',
            vigente_desde=now-timedelta(hours=1), vigente_hasta=now+timedelta(hours=1))
        context = {'proposito': 'DEMOSTRACION_SINTETICA_SIN_IDENTIDAD_NI_FIRMA'}
        presentation = presentar_documento(fixture.s.pk, document.pk, actor=actor,
            contexto=context, clave_idempotencia=uuid.uuid4())
        recibir_respuesta_documental(fixture.s.pk, presentation.pk, actor=actor,
            contexto=context, clave_idempotencia=uuid.uuid4(),
            sha256_presentacion=presentation.sha256, aceptado=answer)
    site = AdminSite(name='admin')
    site.site_header = 'CrediHoy - PRUEBA LOCAL SINTETICA 9G/9H'
    site.site_title = 'Sin identidad, firma ni servicios institucionales'
    site.register(Solicitud, SolicitudAdmin)
    site.register(AceptacionDocumento, AceptacionDocumentoAdmin)
    site.register(EventoDocumental, EventoDocumentalAdmin)
    site.register(VersionDocumental, VersionDocumentalAdmin)
    routes = ModuleType('documentos_demo_urls'); routes.urlpatterns = [path('admin/', site.urls)]
    settings.ROOT_URLCONF = routes
    print(f'DEMO_SINTETICA http://127.0.0.1:{port}/admin/solicitudes/solicitud/?credihoy=acreditado', flush=True)
    print('Fixture: operador-funcional-sintetico / synthetic-only; no usar credenciales institucionales.', flush=True)
    run('127.0.0.1', port, StaticFilesHandler(get_wsgi_application()), threading=False)


def ejecutar(database, args):
    configurar(database,args.autoservicio,args.autorizacion_personal)
    if args.pdf_ensayo:
        from django.conf import settings
        settings.CREDIHOY_PDF_ENSAYO_TEST_ARTIFACT_DIR=str(args.artifact_dir) if args.artifact_dir else ''
    original_connect = socket.socket.connect

    def local_connect(sock, address):
        if (database['ENGINE'].endswith('postgresql') and isinstance(address, tuple)
                and address[0] == '127.0.0.1' and address[1] == database['PORT']):
            return original_connect(sock, address)
        raise AssertionError('RED_INSTITUCIONAL_PROHIBIDA')

    with ExitStack() as stack:
        stack.enter_context(patch('socket.socket.connect', local_connect))
        for name in ('requests.sessions.Session.request', 'dotenv.load_dotenv'):
            stack.enter_context(patch(name, side_effect=AssertionError('PROVEEDOR_O_ENV_PROHIBIDO')))
        import django
        django.setup()
        if args.pdf_overlay:
            import importlib,types,tarfile,hashlib
            with tarfile.open(args.pdf_overlay,'r:') as archive:
                members=archive.getmembers()
                allowed={'apps/utils/pdf.py','apps/xcore_consumo/services/credihoy_v2/unico/simulacion_lectura.py',
                    'apps/xcore_consumo/services/credihoy_v2/unico/publico_institucional.py',
                    'apps/documentos/formularios_credihoy.py','apps/xcore_consumo/assets/COLO-FO.001_v13_20260701.pdf',
                    'apps/documentos/services.py'}
                if {m.name for m in members}!=allowed or len(members)!=len(allowed) or any(not m.isfile() for m in members):
                    raise ValueError('OVERLAY_DOCUMENTAL_NO_ADMITIDO')
                for member in members:
                    if not member.name.endswith('.py'):continue
                    raw=archive.extractfile(member).read();name=member.name[:-3].replace('/','.')
                    module=types.ModuleType(name);module.__package__=name.rsplit('.',1)[0]
                    module.__file__=str(ROOT/member.name)
                    sys.modules[name]=module;exec(compile(raw,module.__file__,'exec'),module.__dict__)
                    setattr(importlib.import_module(module.__package__),name.rsplit('.',1)[1],module)
            print('OVERLAY_PAQUETE_DOCUMENTAL_SHA256='+hashlib.sha256(args.pdf_overlay.read_bytes()).hexdigest(),flush=True)
        from django.core.management import call_command
        if args.generar_migracion:
            call_command('makemigrations', 'documentos', verbosity=1)
            return 0
        if args.check:
            call_command('check', verbosity=1)
            call_command('makemigrations', 'documentos', check=True, dry_run=True, verbosity=1)
            return 0
        if args.demo:
            demo(args.port)
            return 0
        if args.plan:
            call_command('migrate',plan=True,verbosity=1,interactive=False)
        from django.test.runner import DiscoverRunner
        labels = ['tools.test_credihoy_bandeja', 'tools.cd8_test_estado',
            'tools.test_credihoy_titular', 'tools.test_credihoy_institucional', 'tools.test_credihoy_publico',
            'tools.test_credihoy_contrato_decision', 'apps.documentos.tests',
            'apps.documentos.tests_historial', 'apps.documentos.tests_paquetes',
            'tools.test_credihoy_titular_perfil', 'tools.test_credihoy_vinculacion',
            'tools.test_credihoy_publico_perfil', 'tools.test_credihoy_autoservicio',
            'tools.test_credihoy_borrador_privilegios','tools.test_credihoy_autoservicio_release']
        if args.autoservicio:
            labels=['tools.test_credihoy_autoservicio','tools.test_credihoy_borrador_privilegios',
                'tools.test_credihoy_autoservicio_release','tools.test_credihoy_titular_perfil']
        if args.autorizacion_personal:
            labels=['tools.test_credihoy_autorizacion_personal','tools.test_credihoy_autorizacion_privilegios',
                'tools.test_credihoy_integracion_financiera']
        if args.pdf_ensayo:
            labels=['tools.test_credihoy_pdf_ensayo','tools.test_credihoy_oferta_pdf_local',
                'tools.test_credihoy_politica_digital.OfficialPdfTests',
                'apps.integraciones.autentic.tests.test_custodia_verificacion.CustodyTests',
                'apps.integraciones.autentic.tests.test_custodia_verificacion.PostgreSQLJournalTests',
                'tools.test_credihoy_recorridos',
                'tools.test_credihoy_integracion_financiera.PersistedIntegrationTests.test_document001_requires_matching_contract_and_rejects_stale_or_tampered_custody',
                'tools.test_credihoy_integracion_financiera.PersistedIntegrationTests.test_native_offer_keeps_exact_quota_without_requiring_local_payment_plan',
                'tools.test_credihoy_integracion_financiera.PersistedIntegrationTests.test_official_credit_form_cannot_use_an_unreconciled_first_date',
                'apps.integraciones.autentic.tests.test_flujo_documental',
                'tools.test_credihoy_publico.PublicAccessTests.test_csrf_cookie_and_origin_are_required_even_without_django_auth',
                'tools.test_credihoy_publico.PublicAccessTests.test_other_case_cannot_reuse_quote_or_mutate_tracking']
            if args.pdf_overlay:
                # The bounded document release preserves the installed sender;
                # the unrelated local signature inspector is not in its manifest.
                labels=[label for label in labels if not label.startswith('apps.integraciones.autentic.tests.test_custodia_verificacion.')]
        if args.pdf_ensayo:
            class ReceiptRunner(DiscoverRunner):
                def run_suite(self,suite,**kwargs):
                    self.actual_result=super().run_suite(suite,**kwargs)
                    return self.actual_result
            runner=ReceiptRunner(verbosity=2,interactive=False)
            failures=runner.run_tests(labels);result=runner.actual_result
            if args.artifact_dir and args.artifact_dir.is_dir():
                receipt={'motor':database['ENGINE'],'tests':result.testsRun,'fallos':len(result.failures),
                    'errores':len(result.errors),'omitidos':len(result.skipped),'exitFailures':failures,
                    'labels':labels,'sinDotEnv':True,'proveedoresBloqueados':True,
                    'dockerEndpointLocal':_DOCKER_LOCAL_ENDPOINT,'publicado':False,'Oracle':0,'OTP':0,'HDC':0,'C4':0,'START':0}
                (args.artifact_dir/'suite.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
            return failures
        return DiscoverRunner(verbosity=2, interactive=False).run_tests(labels)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres', action='store_true')
    parser.add_argument('--pdf-ensayo',action='store_true',help='PDF de simulacion no contractual y regresiones previas, solo fixtures sinteticos.')
    parser.add_argument('--pdf-overlay',type=Path,help='Solo pruebas: bytes del paquete documental, sin escribir codigo instalado.')
    parser.add_argument('--artifact-dir',type=Path,help='Directorio nuevo dentro de backend/tools para artefactos sinteticos.')
    parser.add_argument('--autoservicio',action='store_true',help='Solo contrato propietario/borrador, sin apps de proveedores.')
    parser.add_argument('--autorizacion-personal',action='store_true',help='Autorizacion del titular con rutas versionadas del canal, sin proveedores.')
    parser.add_argument('--plan',action='store_true',help='Muestra migrate --plan en PostgreSQL desechable antes de las pruebas.')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--generar-migracion', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--port', type=int, default=54933)
    args = parser.parse_args()
    if args.pdf_overlay and (not args.pdf_ensayo or not args.pdf_overlay.is_file() or args.pdf_overlay.is_symlink()):
        parser.error('Overlay solo para PDF ensayo aislado desde archivo regular existente.')
    if args.pdf_ensayo and (args.autoservicio or args.autorizacion_personal or args.demo
            or args.check or args.generar_migracion or args.plan):
        parser.error('PDF ensayo usa solo prueba aislada; no otros modos operativos.')
    if args.artifact_dir:
        root=(ROOT/'tools').resolve();target=args.artifact_dir.resolve()
        if not args.pdf_ensayo or target==root or not target.is_relative_to(root) or target.exists():
            parser.error('Artefactos solo en directorio nuevo de backend/tools con --pdf-ensayo.')
        args.artifact_dir=target
    if args.plan and (not args.postgres or args.demo or args.check or args.generar_migracion):
        parser.error('Plan solo antes de pruebas PostgreSQL locales desechables.')
    if args.autoservicio and (args.demo or args.check or args.generar_migracion):
        parser.error('Autoservicio prueba solo el modulo existente; no demo, migraciones o check documental.')
    if args.autorizacion_personal and (args.autoservicio or args.demo or args.check or args.generar_migracion):
        parser.error('Autorizacion personal solo admite pruebas focalizadas, sin demo o migraciones generadas.')
    if (args.demo and (args.postgres or args.check or args.generar_migracion)) or not 1024 <= args.port <= 65535:
        parser.error('Demo exclusivamente SQLite en memoria; puerto local valido requerido.')
    if args.postgres:
        with postgres_efimero() as (database, name):
            print('POSTGRES_LOCAL_AISLADO=' + name, flush=True)
            failures = ejecutar(database, args)
    else:
        failures = ejecutar({'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}, args)
    print(json.dumps({'motor': 'PostgreSQL 16' if args.postgres else 'SQLite',
        'fallos': failures, 'publicado': False, 'Oracle': 0, 'HDC': 0, 'SMS': 0, 'START': 0}), flush=True)
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
