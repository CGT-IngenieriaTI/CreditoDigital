"""Profile tests with synthetic private files; no .env, providers or real DB."""
import base64
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import patch
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase


class ProfileTests(SimpleTestCase):
    def test_health_remains_registered_when_identity_or_relay_is_enabled(self):
        from core.views import HealthcheckView
        from django.test import RequestFactory,override_settings
        for profile in ('publico','institucional'):
            root='core.urls_credihoy_'+profile
            with override_settings(ROOT_URLCONF=root,CREDIHOY_ACCESO_MANTENIMIENTO=False,
                    CREDIHOY_PUBLICO_ENABLED=profile=='publico',
                    CREDIHOY_INSTITUCIONAL_ENABLED=profile=='institucional',
                    CREDIHOY_SEGUIMIENTO_ENABLED=False),patch('core.views.connection') as db, \
                    patch('requests.sessions.Session.request',side_effect=AssertionError('NO_PROVIDER')):
                urls=runpy.run_module(root)['urlpatterns']
                self.assertEqual(sum(str(v.pattern).endswith('/salud/') for v in urls),1)
                self.assertGreater(len(urls),1)
                db.cursor.return_value.__enter__.return_value.fetchone.return_value=(1,)
                response=HealthcheckView.as_view()(RequestFactory().get('/salud/'))
                self.assertEqual(response.status_code,200)
                packet=json.loads(response.content)
                self.assertEqual(packet['estado'],'HABILITADO')
                self.assertEqual(packet['accesoDisponible'],profile=='publico')
                self.assertEqual(packet['releHabilitado'],profile=='institucional')
                self.assertFalse(packet['seguimientoDisponible'])
                db.cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')
                db.cursor.side_effect=RuntimeError('SYNTHETIC_DB_ERROR')
                failed=HealthcheckView.as_view()(RequestFactory().get('/salud/'))
                self.assertEqual(failed.status_code,503)
                self.assertEqual(json.loads(failed.content),{'estado':'NO_DISPONIBLE'})

    def test_pilot_health_contract_is_unchanged_and_never_uses_9a_database_probe(self):
        from core.views import HealthcheckView
        from django.test import RequestFactory,override_settings
        with override_settings(ROOT_URLCONF='core.urls',CREDIHOY_ACCESO_MANTENIMIENTO=False), \
                patch('core.views.connection') as db:
            response=HealthcheckView.as_view()(RequestFactory().get('/api/v1/health/'))
            self.assertEqual(json.loads(response.content),{'status':'ok','service':'credito-digital'})
            db.cursor.assert_not_called()

    def test_functional_private_profiles_boot_with_real_routes_without_network(self):
        self.config.update(verifyTransporte='RELE_HTTP',institucional=True,
            seguimientoHabilitado=False,titularUrl='https://owner.example.invalid',autoservicioHabilitado=True,
            institucionalRedes=['192.0.2.10/32'],
            ensayo={'modo':'ENSAYO_HTTPS_RESTRINGIDO','aperturaPublica':False,
                'avisoFormalAprobado':False,'referenciaOperativa':'SYNTHETIC_ONLY',
                'solicitudes':['00000000-0000-4000-8000-000000000001'],'operadores':['synthetic']})
        (self.root/'CREDIHOY_PUBLICO_CONFIG').write_text(json.dumps(self.config))
        for key in ('CREDIHOY_INSTITUCIONAL_SECRET','CREDIHOY_PUBLICO_EDGE_SECRET',
                'TWILIO_VERIFY_API_KEY_SID','TWILIO_VERIFY_API_KEY_SECRET','CREDIHOY_TITULAR_SECRET'):
            file=self.root/key;file.write_text(key+'_SYNTHETIC_ONLY_'+'s'*64);file.chmod(0o600)
            self.env[key+'_FILE']=str(file)
        child="""
import socket
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
import django
django.setup()
from django.conf import settings
from django.urls import resolve
assert not settings.CREDIHOY_ACCESO_MANTENIMIENTO
assert settings.SESSION_COOKIE_SECURE and settings.SESSION_COOKIE_HTTPONLY
assert settings.CSRF_COOKIE_SECURE and settings.SESSION_COOKIE_SAMESITE=='Strict'
assert settings.CREDIHOY_SEGUIMIENTO_ENABLED is False
assert settings.CREDIHOY_AUTOSERVICIO_ENABLED is True
if getattr(settings,'CREDIHOY_INSTITUCIONAL_ENABLED',False):
    assert settings.CREDIHOY_TITULAR_URL=='https://owner.example.invalid'
    assert len(settings.CREDIHOY_TITULAR_SECRET)>=48
    assert settings.CREDIHOY_SEGUIMIENTO_DB_ALIAS==''
assert resolve('/api/interno/credihoy/acceso/verify/enviar/' if getattr(settings,'CREDIHOY_INSTITUCIONAL_ENABLED',False)
    else '/api/v1/credihoy/publico/inicio/')
assert resolve('/api/interno/credihoy/acceso/salud/' if getattr(settings,'CREDIHOY_INSTITUCIONAL_ENABLED',False)
    else '/api/v1/credihoy/publico/salud/')
"""
        for profile in ('core.settings_credihoy_restringido','core.settings_credihoy_institucional'):
            env={k:os.environ[k] for k in ('SystemRoot','PATH','TEMP','TMP') if k in os.environ}
            env.update(self.env,DJANGO_SETTINGS_MODULE=profile,DJANGO_SKIP_DOTENV='1',CREDIHOY_PUBLICO_ENABLED='1')
            result=subprocess.run([sys.executable,'-B','-c',child],env=env,
                cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_maintenance_boot_needs_no_identity_client_or_verify_credentials(self):
        self.env['DJANGO_SETTINGS_MODULE']='core.settings_credihoy_restringido'
        self.config.update(verifyTransporte='RELE_HTTP',institucional=True,
            institucionalRedes=['192.0.2.10/32'],
            ensayo={'modo':'ENSAYO_HTTPS_RESTRINGIDO','aperturaPublica':False,
                'avisoFormalAprobado':False,'referenciaOperativa':'SYNTHETIC_ONLY',
                'solicitudes':['00000000-0000-4000-8000-000000000001'],'operadores':['synthetic']})
        (self.root/'CREDIHOY_PUBLICO_CONFIG').write_text(json.dumps(self.config))
        for key in ('API_DATA_CREDIHOY_CLIENT_ID','API_DATA_CREDIHOY_CLIENT_SECRET',
                'TWILIO_ACCOUNT_SID','TWILIO_AUTH_TOKEN'):
            self.env.pop(key+'_FILE')
        private_key=self.root/'institutional';private_key.write_text('r'*64);private_key.chmod(0o600)
        edge=self.root/'edge';edge.write_text('e'*64);edge.chmod(0o600)
        self.env.update(CREDIHOY_INSTITUCIONAL_SECRET_FILE=str(private_key),
            CREDIHOY_PUBLICO_EDGE_SECRET_FILE=str(edge))
        public=self.load()
        self.assertTrue(public['CREDIHOY_ACCESO_MANTENIMIENTO'])
        self.assertEqual(public['API_DATA_CREDIHOY_CLIENT_SECRET'],'')
        module=ModuleType('core.settings_credihoy_publico');module.__dict__.update(public)
        with patch.dict(os.environ,self.env,clear=True),patch.dict(sys.modules,{'core.settings_credihoy_publico':module}):
            private=runpy.run_module('core.settings_credihoy_restringido')
        self.assertNotIn('apps.solicitudes',private['INSTALLED_APPS'])
        module=ModuleType('core.settings_credihoy_restringido');module.__dict__.update(private)
        with patch.dict(os.environ,self.env,clear=True),patch.dict(sys.modules,{'core.settings_credihoy_restringido':module}):
            relay=runpy.run_module('core.settings_credihoy_institucional')
        self.assertFalse(relay['CREDIHOY_INSTITUCIONAL_ENABLED'])
        self.assertEqual(relay['TWILIO_VERIFY_API_KEY_SECRET'],'')
        # A fresh registry must boot without business apps, not just mocked imports.
        child = """
import socket
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
import django
django.setup()
from django.apps import apps
assert not apps.is_installed('apps.solicitudes')
from django.test import Client
from unittest.mock import patch
from django.conf import settings
path=('/api/interno/credihoy/acceso/salud/' if settings.ROOT_URLCONF.endswith('institucional')
    else '/api/v1/credihoy/publico/salud/')
with patch('core.views.connection') as db:
    db.cursor.return_value.__enter__.return_value.fetchone.return_value=(1,)
    response=Client().get(path,HTTP_HOST='access.example.invalid:18049',
        REMOTE_ADDR='192.0.2.41',HTTP_X_FORWARDED_FOR='192.0.2.10' if 'interno' in path else '192.0.2.24',
        HTTP_X_FORWARDED_PROTO='https')
    assert response.status_code==200, response.status_code
    assert response.json()['accesoDisponible'] is False
"""
        for profile in ('core.settings_credihoy_restringido','core.settings_credihoy_institucional'):
            env={k:os.environ[k] for k in ('SystemRoot','PATH','TEMP','TMP') if k in os.environ}
            env.update(self.env,DJANGO_SETTINGS_MODULE=profile,DJANGO_SKIP_DOTENV='1')
            result=subprocess.run([sys.executable,'-B','-c',child],env=env,
                cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
        self.env['CREDIHOY_PUBLICO_ENABLED']='1'
        with self.assertRaises(ImproperlyConfigured):self.load()

    def test_maintenance_urlconfs_expose_only_health_not_identity_or_verify(self):
        from django.test import override_settings
        with override_settings(CREDIHOY_ACCESO_MANTENIMIENTO=True):
            for module in ('core.urls_credihoy_publico','core.urls_credihoy_institucional'):
                urls=runpy.run_module(module)['urlpatterns']
                self.assertEqual(len(urls),1)
                self.assertTrue(str(urls[0].pattern).endswith('/salud/'))

    def test_health_requires_database_and_reports_otp_disabled(self):
        from core.views import HealthcheckView
        from django.test import RequestFactory,override_settings
        request=RequestFactory().get('/salud/')
        with override_settings(CREDIHOY_ACCESO_MANTENIMIENTO=True),patch('core.views.connection') as db:
            cursor=db.cursor
            cursor.return_value.__enter__.return_value.fetchone.return_value=(1,)
            response=HealthcheckView.as_view()(request)
            self.assertEqual(response.status_code,200)
            data=json.loads(response.content)
            self.assertFalse(data['accesoDisponible']);self.assertFalse(data['seguimientoDisponible'])
            cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')
        with override_settings(CREDIHOY_ACCESO_MANTENIMIENTO=True),patch('core.views.connection') as db:
            db.cursor.side_effect=RuntimeError('PRIVATE')
            response=HealthcheckView.as_view()(request)
            self.assertEqual(response.status_code,503)
            self.assertNotIn(b'PRIVATE',response.content)

    def setUp(self):
        self.temp=TemporaryDirectory(prefix='credihoy-9a-profile-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config={'origen':'https://access.example.invalid:18049',
            'avisoVersion':'SYNTHETIC','avisoTexto':'SYNTHETIC ONLY',
            'botSitekey':'synthetic','verifySid':'VA'+'a'*32,'verifyConsentimientoSid':'VA'+'b'*32,
            'canales':['SMS'],'retencionDias':30,'identidadesHmac':['a'*64],
            'redesAutorizadas':['192.0.2.24/32'],'proxiesConfiables':['192.0.2.41/32'],
            'autorizacionHasta':'2099-01-01T00:00:00+00:00','autorizacionReferencia':'SYNTHETIC'}
        self.config['validacionInstitucional']={'estado':'BORRADOR','sesionSegundos':900,
            'otpSegundos':300,'limpiezaTemporalMaxSegundos':60}
        self.env={'POSTGRES_DB':'synthetic','POSTGRES_USER':'synthetic','POSTGRES_HOST':'db.invalid',
            'POSTGRES_PORT':'5432','API_DATA_CREDIHOY_BASE_URL':'https://api.example.invalid',
            'CREDIHOY_PUBLICO_ENABLED':'0'}
        secrets={'CREDIHOY_PUBLICO_SECRET_KEY':'SYNTHETIC_SECRET_0123456789_abcdefghijklmnopqrstuvwxyz',
            'CREDIHOY_PUBLICO_CONFIG':json.dumps(self.config),
            'CREDIHOY_PUBLICO_BOT_SECRET':'synthetic','CREDIHOY_PUBLICO_OTP_AES_KEY_B64':base64.b64encode(b'a'*32).decode(),
            'TWILIO_ACCOUNT_SID':'synthetic','TWILIO_AUTH_TOKEN':'synthetic',
            'API_DATA_CREDIHOY_CLIENT_ID':'synthetic','API_DATA_CREDIHOY_CLIENT_SECRET':'synthetic',
            'POSTGRES_PASSWORD':'synthetic','CREDIHOY_PUBLICO_CELERY_BROKER_URL':'redis://broker.invalid/9'}
        for key,value in secrets.items():
            p=self.root/key;p.write_text(value,encoding='utf-8');p.chmod(0o600)
            self.env[key+'_FILE']=str(p)

    def load(self):
        # Do not import the real base settings or read a development .env.
        base=ModuleType('core.settings')
        base.INSTALLED_APPS=['django.contrib.admin']
        base.MIDDLEWARE=['django.middleware.security.SecurityMiddleware']
        base.REST_FRAMEWORK={}
        with patch.dict(os.environ,self.env,clear=True),patch.dict(sys.modules,{'core.settings':base}):
            return runpy.run_module('core.settings_credihoy_publico')

    def test_separate_profile_is_closed_by_default_without_financial_routes(self):
        p=self.load()
        self.assertFalse(p['CREDIHOY_PUBLICO_ENABLED']);self.assertFalse(p['DEBUG'])
        self.assertEqual(p['ROOT_URLCONF'],'core.urls_credihoy_publico')
        self.assertEqual(p['SESSION_COOKIE_NAME'],'credihoy_publico_session')
        self.assertEqual(p['CSRF_COOKIE_NAME'],'credihoy_publico_csrf')
        self.assertIsNone(p['SESSION_COOKIE_DOMAIN']);self.assertEqual(p['SESSION_COOKIE_SAMESITE'],'Strict')
        self.assertTrue(p['SESSION_COOKIE_SECURE']);self.assertTrue(p['SESSION_COOKIE_HTTPONLY'])
        self.assertEqual(p['CELERY_TASK_DEFAULT_QUEUE'],'credihoy_publico')
        self.assertFalse(p['RISK_GATEWAY_CONSUMO_ENABLED']);self.assertFalse(p['CREDIHOY_COTIZACION_ENABLED'])

    def test_public_prefix_scopes_only_own_cookies_and_script_name(self):
        self.config.update(origen='https://consulta.congente.coop:8047',prefijoPublico='/credihoy')
        (self.root/'CREDIHOY_PUBLICO_CONFIG').write_text(json.dumps(self.config))
        profile=self.load()
        self.assertEqual(profile['SESSION_COOKIE_PATH'],'/credihoy/')
        self.assertEqual(profile['CSRF_COOKIE_PATH'],'/credihoy/')
        self.assertEqual(profile['FORCE_SCRIPT_NAME'],'/credihoy')
        self.assertEqual(profile['CSRF_TRUSTED_ORIGINS'],['https://consulta.congente.coop:8047'])
        self.assertEqual(profile['CREDIHOY_PUBLICO_MODO'],'PUBLICO_HTTPS')
        self.assertFalse(profile['CREDIHOY_PUBLICO_RESTRINGIDO'])
        self.assertTrue(profile['SESSION_COOKIE_SECURE']);self.assertTrue(profile['SESSION_COOKIE_HTTPONLY'])
        self.assertIsNone(profile['SESSION_COOKIE_DOMAIN'])

    def test_public_prefix_rejects_traversal_other_apps_and_external_origin(self):
        for value in ('/','/credihoy/','/admin','/credihoy/../admin','//evil.invalid',None):
            self.config['prefijoPublico']=value
            (self.root/'CREDIHOY_PUBLICO_CONFIG').write_text(json.dumps(self.config))
            with self.subTest(value=value),self.assertRaises(ImproperlyConfigured):self.load()

    def test_missing_secret_file_rejects_start_without_exposing_values(self):
        self.env.pop('CREDIHOY_PUBLICO_SECRET_KEY_FILE')
        with self.assertRaises(ImproperlyConfigured) as error:self.load()
        self.assertNotIn('SYNTHETIC_SECRET',str(error.exception))

    def test_approved_notice_and_https_origin_are_mandatory(self):
        for key,value in [('avisoTexto',''),('origen','http://access.example.invalid'),
                ('origen','https://access.example.invalid/path'),('retencionDias',0),('canales',[])]:
            c={**self.config,key:value}
            (self.root/'CREDIHOY_PUBLICO_CONFIG').write_text(json.dumps(c),encoding='utf-8')
            with self.assertRaises(ImproperlyConfigured):self.load()

    def test_private_profile_has_only_its_own_database_and_no_pilot_credentials(self):
        public=self.load()
        public['cfg']['ensayo']={'modo':'ENSAYO_HTTPS_RESTRINGIDO','aperturaPublica':False,
            'avisoFormalAprobado':False,'referenciaOperativa':'SYNTHETIC_ONLY',
            'solicitudes':['00000000-0000-4000-8000-000000000001'],'operadores':['synthetic']}
        module=ModuleType('core.settings_credihoy_publico')
        module.__dict__.update(public)
        edge=self.root/'edge';edge.write_text('x'*64,encoding='utf-8');edge.chmod(0o600)
        with patch.dict(os.environ,{**self.env,'CREDIHOY_PUBLICO_EDGE_SECRET_FILE':str(edge)},clear=True),\
                patch.dict(sys.modules,{'core.settings_credihoy_publico':module}):
            private=runpy.run_module('core.settings_credihoy_restringido')
        self.assertEqual(set(private['DATABASES']),{'default'})
        self.assertTrue(private['CREDIHOY_PUBLICO_SOLICITUDES_POR_API'])
        self.assertFalse(private['CREDIHOY_SEGUIMIENTO_ENABLED'])
        self.assertTrue(private['CELERY_TASK_ALWAYS_EAGER'])
        self.assertTrue(private['SESSION_COOKIE_HTTPONLY'])
        self.assertTrue(private['SESSION_COOKIE_SECURE'])
        self.assertEqual(private['SECURE_HSTS_SECONDS'],0)

    def test_http_verify_profile_never_loads_twilio_credentials_in_public_backend(self):
        self.config.update(verifyTransporte='RELE_HTTP',institucionalUrl='https://relay.example.invalid')
        (self.root/'CREDIHOY_PUBLICO_CONFIG').write_text(json.dumps(self.config),encoding='utf-8')
        key=self.root/'institutional';key.write_text('SYNTHETIC_'+'s'*64,encoding='utf-8');key.chmod(0o600)
        self.env['CREDIHOY_INSTITUCIONAL_SECRET_FILE']=str(key)
        self.env.pop('TWILIO_ACCOUNT_SID_FILE');self.env.pop('TWILIO_AUTH_TOKEN_FILE')
        profile=self.load()
        self.assertEqual(profile['TWILIO_ACCOUNT_SID'],'')
        self.assertEqual(profile['TWILIO_AUTH_TOKEN'],'')
        self.assertEqual(profile['CREDIHOY_VERIFY_TRANSPORTE'],'RELE_HTTP')

    def test_private_boot_does_not_require_dummy_turnstile_or_unused_shared_broker(self):
        self.env['DJANGO_SETTINGS_MODULE']='core.settings_credihoy_restringido'
        self.config['ensayo']={'modo':'ENSAYO_HTTPS_RESTRINGIDO','aperturaPublica':False,
            'avisoFormalAprobado':False,'referenciaOperativa':'SYNTHETIC_ONLY',
            'solicitudes':['00000000-0000-4000-8000-000000000001'],'operadores':['synthetic']}
        self.config.pop('botSitekey')
        (self.root/'CREDIHOY_PUBLICO_CONFIG').write_text(json.dumps(self.config),encoding='utf-8')
        self.env.pop('CREDIHOY_PUBLICO_BOT_SECRET_FILE')
        self.env.pop('CREDIHOY_PUBLICO_CELERY_BROKER_URL_FILE')
        public=self.load()
        self.assertEqual(public['CREDIHOY_PUBLICO_BOT_SITEKEY'],'')
        self.assertEqual(public['CREDIHOY_PUBLICO_BOT_SECRET'],'')
        self.assertIsNone(public['CELERY_BROKER_URL'])
        module=ModuleType('core.settings_credihoy_publico');module.__dict__.update(public)
        edge=self.root/'edge';edge.write_text('x'*64);edge.chmod(0o600)
        with patch.dict(os.environ,{**self.env,'CREDIHOY_PUBLICO_EDGE_SECRET_FILE':str(edge)},clear=True), \
                patch.dict(sys.modules,{'core.settings_credihoy_publico':module}):
            private=runpy.run_module('core.settings_credihoy_restringido')
        self.assertTrue(private['CELERY_TASK_ALWAYS_EAGER'])
        self.assertTrue(private['SESSION_COOKIE_SECURE'])
        self.assertFalse(private['CREDIHOY_SEGUIMIENTO_ENABLED'])
        for profile in ('core.settings_credihoy_publico','core.settings'):
            self.env['DJANGO_SETTINGS_MODULE']=profile
            with self.subTest(profile=profile),self.assertRaises(ImproperlyConfigured):self.load()

    def test_private_boot_requires_its_explicit_closed_mode(self):
        self.env['DJANGO_SETTINGS_MODULE']='core.settings_credihoy_restringido'
        for ensayo in ({},{'modo':'PUBLICO','aperturaPublica':True},
                {'modo':'ENSAYO_HTTPS_RESTRINGIDO','aperturaPublica':True,
                    'avisoFormalAprobado':False,'referenciaOperativa':'SYNTHETIC'}):
            self.config['ensayo']=ensayo
            (self.root/'CREDIHOY_PUBLICO_CONFIG').write_text(json.dumps(self.config),encoding='utf-8')
            with self.subTest(ensayo=ensayo),self.assertRaises(ImproperlyConfigured):self.load()

    def test_institutional_profile_tracks_by_http_and_has_no_case_database_alias(self):
        public=self.load()
        public.update(CREDIHOY_VERIFY_TRANSPORTE='RELE_HTTP',CREDIHOY_INSTITUCIONAL_SECRET='r'*64,
            CREDIHOY_SEGUIMIENTO_ENABLED=True)
        public['cfg'].update(institucional=True,institucionalRedes=['192.0.2.10/32'],
            titularUrl='https://owner.example.invalid')
        module=ModuleType('core.settings_credihoy_restringido');module.__dict__.update(public)
        for key,value in {'TWILIO_VERIFY_API_KEY_SID':'SK'+'b'*32,'TWILIO_VERIFY_API_KEY_SECRET':'s'*32,
                          'CREDIHOY_TITULAR_SECRET':'o'*64}.items():
            p=self.root/key;p.write_text(value);p.chmod(0o600);self.env[key+'_FILE']=str(p)
        with patch.dict(os.environ,self.env,clear=True),\
                patch.dict(sys.modules,{'core.settings_credihoy_restringido':module}):
            relay=runpy.run_module('core.settings_credihoy_institucional')
            self.assertEqual(relay['CREDIHOY_SEGUIMIENTO_TRANSPORTE'],'HTTP_PROPIETARIO')
            self.assertEqual(relay['CREDIHOY_SEGUIMIENTO_DB_ALIAS'],'')
            self.assertEqual(relay['CREDIHOY_TITULAR_URL'],'https://owner.example.invalid')
            self.assertEqual(set(relay['DATABASES']),{'default'})
            self.assertEqual(relay['TWILIO_AUTH_TOKEN'],'')
            (self.root/'CREDIHOY_TITULAR_SECRET').write_text('r'*64)
            with self.assertRaises(ImproperlyConfigured):runpy.run_module('core.settings_credihoy_institucional')

    def test_authentication_relay_starts_without_owner_secret_or_case_source(self):
        public=self.load()
        public.update(CREDIHOY_VERIFY_TRANSPORTE='RELE_HTTP',CREDIHOY_INSTITUCIONAL_SECRET='r'*64,
            CREDIHOY_SEGUIMIENTO_ENABLED=False)
        public['cfg'].update(institucional=True,institucionalRedes=['192.0.2.10/32'])
        module=ModuleType('core.settings_credihoy_restringido');module.__dict__.update(public)
        for key,value in {'TWILIO_VERIFY_API_KEY_SID':'SK'+'b'*32,
                          'TWILIO_VERIFY_API_KEY_SECRET':'s'*32}.items():
            p=self.root/key;p.write_text(value);p.chmod(0o600);self.env[key+'_FILE']=str(p)
        with patch.dict(os.environ,self.env,clear=True),\
                patch.dict(sys.modules,{'core.settings_credihoy_restringido':module}):
            relay=runpy.run_module('core.settings_credihoy_institucional')
        self.assertEqual(relay['CREDIHOY_TITULAR_SECRET'],'')
        self.assertEqual(relay['CREDIHOY_TITULAR_URL'],'')
        self.assertEqual(relay['CREDIHOY_SEGUIMIENTO_DB_ALIAS'],'')
        self.assertTrue(relay['CREDIHOY_INSTITUCIONAL_ENABLED'])
        self.assertFalse(relay['CREDIHOY_PUBLICO_ENABLED'])
