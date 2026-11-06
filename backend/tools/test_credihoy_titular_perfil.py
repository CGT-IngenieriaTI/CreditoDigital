"""Carga del perfil propietario con custodia sintetica, sin conexiones ni .env."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4


class PerfilPropietarioTests(TestCase):
    def test_perfil_aislado_sin_verify_fuente_readonly_nonce_separado_y_ruta_existente(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in (('source-password', 'synthetic-source-only'),
                    ('historical-password', 'synthetic-historical-only'),
                    ('nonce-password', 'synthetic-nonce-only'), ('session', 'session-synthetic-'*5),
                    ('quote-password','synthetic-quote-only'),('api-token','synthetic-api-token'),
                    ('delegation', 'delegation-synthetic-'*5), ('draft-password','synthetic-draft-only'),
                    ('progress-key',__import__('base64').b64encode(b'p'*32).decode())):
                p = root/name; p.write_text(value); p.chmod(0o600)
            cfg = {'host': 'propietario.example.invalid', 'redes': ['192.0.2.10/32'],
                'proxies': ['192.0.2.11/32'], 'documentosHmac': ['a'*64],
                'solicitudes': [str(uuid4())], 'fuenteReferencia': 'FUENTE-SINTETICA-NO-INSTITUCIONAL',
                'fuente': {'host': '127.0.0.1', 'puerto': 9, 'nombre': 'credito_digital',
                    'usuario': 'credihoy_titular_lector', 'passwordFile': str(root/'source-password')},
                'nonce': {'host': '127.0.0.1', 'puerto': 9, 'nombre': 'credihoy_9a',
                    'usuario': 'credihoy_9a_nonce', 'passwordFile': str(root/'nonce-password')},
                'secretFile': str(root/'session'), 'delegacionFile': str(root/'delegation'),
                'vigenteHasta': (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()}
            path = root/'config.json'; path.write_text(json.dumps(cfg)); path.chmod(0o600)
            script = '''
from copy import deepcopy
from unittest.mock import patch
with patch('socket.socket.connect', side_effect=AssertionError('NO_NETWORK')), \
        patch('dotenv.load_dotenv', side_effect=AssertionError('NO_DOTENV')):
    import django
    django.setup()
    from django.conf import settings as s
    from django.core.management import call_command
    from django.core.exceptions import ImproperlyConfigured
    from django.test import Client
    from django.urls import resolve
    from core.settings_credihoy_titular import configurar_propietario, cfg
    call_command('check', verbosity=0)
    assert resolve('/api/interno/credihoy/solicitudes/titular/').func.view_class.__name__ == 'SolicitudesTitularView'
    assert s.CREDIHOY_TITULAR_ENABLED and not s.CREDIHOY_PUBLICO_ENABLED
    assert s.DATABASES['default']['OPTIONS']['options'] == '-c default_transaction_read_only=on'
    assert s.DATABASES['nonce']['USER'] == 'credihoy_9a_nonce'
    assert s.CELERY_BROKER_URL is None and not s.XCORE_CONSUMO_ORACLE_ENABLED
    assert 'apps.integraciones.risk_gateway.operaciones' not in s.INSTALLED_APPS
    import sys
    from uuid import uuid4
    from apps.xcore_consumo.services.credihoy_v2.unico import publico_controles as controls
    from apps.xcore_consumo.services.credihoy_v2.unico import publico_seguimiento as owner
    with patch.object(controls,'limite',return_value=True) as limit:
        rid=str(uuid4());assert owner.claim_delegation(rid) is True
        limit.assert_called_once_with([('titular_delegacion',rid,1)],using='nonce')
    assert 'apps.xcore_consumo.services.credihoy_v2.unico.publico' not in sys.modules
    assert 'apps.xcore_consumo.services.credihoy_v2.unico.publico_proveedores' not in sys.modules
    assert 'apps.integraciones.risk_gateway.operaciones.models' not in sys.modules
    from apps.xcore_consumo.services.credihoy_v2.unico import cotizacion as quotes
    from apps.xcore_consumo.services.credihoy_v2.unico.publico_institucional import NoDisponible
    try:quotes.operacion_titular({}, {'solicitudId':str(uuid4()),'operacion':'cotizar'},str(uuid4()))
    except NoDisponible:pass
    else:raise AssertionError('CLOSED_QUOTE_MUST_NOT_QUERY_OR_LOAD_GATEWAY')
    assert 'apps.integraciones.risk_gateway.operaciones.models' not in sys.modules
    assert not getattr(s, 'TWILIO_AUTH_TOKEN', '')
    client = Client()
    assert client.post('/api/interno/credihoy/solicitudes/titular/', {}, content_type='application/json',
        HTTP_HOST=cfg['host'], REMOTE_ADDR='192.0.2.99').status_code == 404
    assert client.post('/api/interno/credihoy/solicitudes/titular/', {}, content_type='application/json',
        HTTP_HOST=cfg['host'], REMOTE_ADDR='192.0.2.10', secure=True).status_code == 403
    expired=deepcopy(cfg);expired['vigenteHasta']='2000-01-01T00:00:00+00:00'
    configurar_propietario(expired)
    from django.test import override_settings
    with override_settings(CREDIHOY_PUBLICO_AUTORIZACION_HASTA=expired['vigenteHasta']):
        assert client.post('/api/interno/credihoy/solicitudes/titular/', {}, content_type='application/json',
            HTTP_HOST=cfg['host'], REMOTE_ADDR='192.0.2.10', secure=True).status_code == 404
    for key, field, value in [('fuente','usuario','postgres'), ('fuente','nombre','credihoy_9a'),
            ('nonce','usuario','postgres')]:
        broken = deepcopy(cfg); broken[key][field] = value
        try:
            configurar_propietario(broken)
        except ImproperlyConfigured:
            pass
        else:
            raise AssertionError('UNSAFE_CONFIG')
    closed = deepcopy(cfg); closed['solicitudes'] = []; closed['fuenteHabilitada'] = False
    configurar_propietario(closed)
    assert not s.CREDIHOY_AUTOSERVICIO_ENABLED and not s.CREDIHOY_AUTOSERVICIO_DB_ALIAS
    assert not s.CREDIHOY_COTIZACION_TITULAR_ENABLED and not s.CREDIHOY_COTIZACION_DB_ALIAS
    assert not s.CREDIHOY_OFERTA_CUSTODIA_ENABLED
    quoted=deepcopy(cfg)
    quoted['cotizacion']={'habilitada':False,'conexion':{**cfg['fuente'],
        'usuario':'credihoy_titular_cotizacion','passwordFile':str(__import__('pathlib').Path(cfg['secretFile']).parent/'quote-password')},
        'solicitudes':list(cfg['solicitudes']),'referencia':'SYNTHETIC_ONLY',
        'apiData':{'origen':'https://api.example.invalid','tokenFile':str(__import__('pathlib').Path(cfg['secretFile']).parent/'api-token'),'timeout':15}}
    dbs,_,_=configurar_propietario(quoted)
    for value in (True,1,'true',None):
        invalid_custody=deepcopy(quoted)
        invalid_custody['cotizacion']['custodiaEstudioHabilitada']=value
        try:configurar_propietario(invalid_custody)
        except ImproperlyConfigured:pass
        else:raise AssertionError('CUSTODY_REQUIRES_BOOLEAN_AND_ENABLED_QUOTE')
    custody=deepcopy(quoted)
    custody['cotizacion'].update(habilitada=True,custodiaEstudioHabilitada=True)
    custody_dbs,_,_=configurar_propietario(custody)
    assert custody_dbs==dbs
    assert dbs['default']['OPTIONS']['options']=='-c default_transaction_read_only=on'
    assert dbs['cotizacion']['USER']=='credihoy_titular_cotizacion' and dbs['cotizacion']['NAME']=='credito_digital'
    assert 'options' not in dbs['cotizacion']['OPTIONS']
    prepared=deepcopy(quoted)
    prepared['cotizacion'].pop('apiData');prepared['cotizacion']['solicitudes']=[]
    dbs,_,_=configurar_propietario(prepared)
    assert dbs['cotizacion']['USER']=='credihoy_titular_cotizacion'
    for value in (None,{},False):
        bad=deepcopy(prepared);bad['cotizacion']['apiData']=value
        try:configurar_propietario(bad)
        except ImproperlyConfigured:pass
        else:raise AssertionError('EXPLICIT_INVALID_API_MUST_NOT_BE_IGNORED')
    enabled=deepcopy(prepared);enabled['cotizacion']['habilitada']=True
    for cases in ([],list(cfg['solicitudes'])):
        enabled['cotizacion']['solicitudes']=cases
        try:configurar_propietario(enabled)
        except ImproperlyConfigured:pass
        else:raise AssertionError('ENABLED_QUOTE_REQUIRES_REAL_CASE_AND_API')
    for section,key,value in [('conexion','usuario','credihoy_titular_lector'),
            ('conexion','passwordFile',cfg['fuente']['passwordFile']),('apiData','origen','http://api.example.invalid'),
            ('apiData','tokenFile',cfg['nonce']['passwordFile'])]:
        bad=deepcopy(quoted);bad['cotizacion'][section][key]=value
        try:configurar_propietario(bad)
        except ImproperlyConfigured:pass
        else:raise AssertionError('UNSAFE_QUOTE_CONFIG')
    writing=deepcopy(closed);writing['autoservicioHabilitado']=True
    from apps.xcore_consumo.services.credihoy_v2.unico import progreso_privacidad as pn
    writing['progresoTemporal']={'claveCifradoFile':str(__import__('pathlib').Path(cfg['secretFile']).parent/'progress-key'),
        'validacion':{'version':pn.VERSION,'sha256':pn.SHA256,'consistenciaPoliticaReferencia':'SYNTHETIC_ONLY',
            'respaldoCustodiaReferencia':'SYNTHETIC_ONLY','limpiezaMaxSegundos':60}}
    writing['borradores']={**writing['fuente'],'usuario':'credihoy_titular_borrador',
        'passwordFile':str(__import__('pathlib').Path(cfg['secretFile']).parent/'draft-password')}
    db,_,_=configurar_propietario(writing)
    assert db['default']['USER']=='credihoy_titular_lector'
    assert db['borradores']['USER']=='credihoy_titular_borrador'
    assert db['borradores']['NAME']==db['default']['NAME']
    assert db['default']['OPTIONS']['options']=='-c default_transaction_read_only=on'
    staged=deepcopy(writing);staged['autoservicioHabilitado']=False
    db,_,_=configurar_propietario(staged)
    assert db['borradores']['USER']=='credihoy_titular_borrador'
    assert not s.CREDIHOY_AUTOSERVICIO_ENABLED and not s.CREDIHOY_AUTOSERVICIO_DB_ALIAS
    for field,value in [('usuario','credihoy_titular_lector'),('nombre','credihoy_validacion'),
            ('host','otra-fuente'),('passwordFile',staged['fuente']['passwordFile'])]:
        broken=deepcopy(staged);broken['borradores'][field]=value
        try:configurar_propietario(broken)
        except ImproperlyConfigured:pass
        else:raise AssertionError('DISABLED_FLAG_MUST_NOT_BYPASS_WRITER_ALIAS_VALIDATION')
    historical=deepcopy(writing)
    historical['historico']={'referencia':'SYNTHETIC_CUSTODIAN_ONLY','solicitudes':[str(__import__('uuid').uuid4())],
        'conexion':{**cfg['fuente'],'nombre':'credihoy_validacion','usuario':'credihoy_titular_historico',
                    'passwordFile':str(__import__('pathlib').Path(cfg['secretFile']).parent/'historical-password')}}
    db,_,_=configurar_propietario(historical)
    assert db['historico']['NAME']=='credihoy_validacion'
    assert db['historico']['USER']=='credihoy_titular_historico'
    assert db['borradores']['NAME']==db['default']['NAME']=='credito_digital'
    assert 'search_path=credihoy_lectura,pg_catalog' in db['historico']['OPTIONS']['options']
    active=deepcopy(historical);active['fuenteHabilitada']=True
    db,_,_=configurar_propietario(active)
    assert db['default']['NAME']=='credito_digital' and db['historico']['NAME']=='credihoy_validacion'
    from types import SimpleNamespace
    from apps.solicitudes.models import Solicitud
    from apps.xcore_consumo.services.credihoy_v2.unico import publico_institucional as wire
    with override_settings(DATABASES=db,CREDIHOY_SEGUIMIENTO_DB_ALIAS='historico',
            CREDIHOY_SEGUIMIENTO_SOURCE_REFERENCE='SYNTHETIC_ONLY',CREDIHOY_TITULAR_FUENTE_ENABLED=True,
            CREDIHOY_PUBLICO_ENSAYO={'solicitudes':active['historico']['solicitudes']}), \
            patch.object(owner.transaction,'atomic'), \
            patch.object(owner,'connections',{'historico':SimpleNamespace(vendor='sqlite')}), \
            patch.object(Solicitud.objects,'using') as source:
        source.return_value.filter.return_value.count.return_value=0
        try:owner.read_owned({'numeroDocumento':'0000000001'},{})
        except wire.NoDisponible:pass
        else:raise AssertionError('MISSING_SOURCE_MUST_FAIL_CLOSED')
        source.assert_called_once_with('historico')
    assert 'apps.xcore_consumo.services.credihoy_v2.unico.publico_http' not in sys.modules
    assert 'apps.xcore_consumo.services.credihoy_v2.unico.publico_proveedores' not in sys.modules
    for field,value in [('usuario','postgres'),('nombre','credito_digital'),
        ('passwordFile',cfg['fuente']['passwordFile'])]:
        broken=deepcopy(historical);broken['historico']['conexion'][field]=value
        try:configurar_propietario(broken)
        except ImproperlyConfigured:pass
        else:raise AssertionError('HISTORICAL_SOURCE_MUST_BE_SEPARATE_AND_SCOPED')
    shared=deepcopy(historical);shared['cotizacion']=deepcopy(prepared['cotizacion'])
    shared['cotizacion']['conexion']['passwordFile']=historical['historico']['conexion']['passwordFile']
    try:configurar_propietario(shared)
    except ImproperlyConfigured:pass
    else:raise AssertionError('QUOTE_CANNOT_REUSE_HISTORICAL_CUSTODY')
    broken=deepcopy(historical);broken['fuente']['nombre']='credihoy_validacion'
    try:configurar_propietario(broken)
    except ImproperlyConfigured:pass
    else:raise AssertionError('OPERATIVE_SOURCE_MUST_NOT_BECOME_HISTORICAL')
    for field,value in [('usuario','credihoy_titular_lector'),('nombre','otra_fuente'),
        ('passwordFile',writing['fuente']['passwordFile'])]:
        broken=deepcopy(writing);broken['borradores'][field]=value
        try:configurar_propietario(broken)
        except ImproperlyConfigured:pass
        else:raise AssertionError('READ_ROLE_OR_OTHER_SOURCE_MUST_NOT_WRITE')
    for value in (True, 'false', 0):
        broken = deepcopy(closed); broken['fuenteHabilitada'] = value
        try:
            configurar_propietario(broken)
        except ImproperlyConfigured:
            pass
        else:
            raise AssertionError('EMPTY_SOURCE_MUST_NOT_ENABLE_TRACKING')
print('PERFIL_PROPIETARIO_LOCAL_PASS_SIN_RED')
__import__('pathlib').Path(__import__('os').environ['CREDIHOY_TITULAR_CONFIG_FILE']).write_text(__import__('json').dumps(prepared))
'''
            env = {k: v for k, v in os.environ.items() if not k.startswith(('DJANGO_', 'POSTGRES_',
                'DATABASE_', 'ORACLE_', 'TWILIO_', 'CREDIHOY_', 'API_DATA_', 'RISK_GATEWAY_'))}
            env.update(DJANGO_SETTINGS_MODULE='core.settings_credihoy_titular', DJANGO_SKIP_DOTENV='1',
                CREDIHOY_TITULAR_CONFIG_FILE=str(path))
            run = subprocess.run([sys.executable, '-B', '-c', script], cwd=Path(__file__).resolve().parents[1],
                env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('PERFIL_PROPIETARIO_LOCAL_PASS_SIN_RED', run.stdout)
            quoted_script='''
from unittest.mock import patch
with patch('socket.socket.connect',side_effect=AssertionError('NO_NETWORK')), \
        patch('dotenv.load_dotenv',side_effect=AssertionError('NO_DOTENV')):
 import django;django.setup()
 from django.conf import settings as s
 from django.apps import apps
 from django.db import router
 from django.core.exceptions import PermissionDenied
 from apps.xcore_consumo.services.credihoy_v2.unico.publico_restringido import fuente_cotizacion
 model=apps.get_model('risk_gateway_ops','OperacionGateway')
 assert 'apps.integraciones.risk_gateway.operaciones' in s.INSTALLED_APPS
 assert not s.CREDIHOY_COTIZACION_ENABLED and not s.CREDIHOY_COTIZACION_TITULAR_ENABLED
 assert not s.API_DATA_CREDIHOY_BASE_URL and not s.API_DATA_CREDIHOY_TOKEN
 assert not s.CREDIHOY_COTIZACION_SOLICITUDES
 assert s.DATABASES['default']['OPTIONS']['options']=='-c default_transaction_read_only=on'
 assert not s.CREDIT_PIPELINE_ASYNC and not s.XCORE_CONSUMO_ORACLE_ENABLED
 assert s.RISK_GATEWAY_ENABLED=='N' and s.CELERY_BROKER_URL is None
 with fuente_cotizacion():
  assert router.db_for_read(model)=='cotizacion'
  try:router.db_for_write(model)
  except PermissionDenied:pass
  else:raise AssertionError('GATEWAY_WRITER_FORBIDDEN')
 print('PERFIL_COTIZACION_GATEWAY_SOLO_LECTURA_PASS')
'''
            run=subprocess.run([sys.executable,'-B','-c',quoted_script],cwd=Path(__file__).resolve().parents[1],
                env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertIn('PERFIL_COTIZACION_GATEWAY_SOLO_LECTURA_PASS',run.stdout)
