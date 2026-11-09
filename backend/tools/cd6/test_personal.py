"""Personal CD6 capability and native journal regressions; no real Oracle transport."""
import base64
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from incluirtec.digital_operations.contracts import canonical,normalize_technical_request
from incluirtec.digital_operations.production_preparation import prepare_personal
from incluirtec.digital_operations.production_preflight import require_personal_start_boundary
from .condiciones import FichaError,request_final
from .grant import PERSONAL_DOMAIN,PERSONAL_PURPOSE,validar_delegacion_personal
from .journal import Journal
from .test_condiciones import fixture,NOW
from .test_caso_operativo import case_message,configuration,CasePort
from .worker import operate_personal,Port


class PersonalTests(TestCase):
    def setUp(self):
        base,terms,domains=fixture()
        payload,_=request_final(base,terms,domains,str(uuid4()),'SYNTHETIC','SYNTHETIC',now=NOW)
        payload['solicitudId']=str(uuid4())
        metadata=case_message(payload)
        payload=normalize_technical_request(payload)
        payload['contextoLinix']['entrada113']={'tipoConsulta':'2','modalidadSimular':'M','datoCalcularSimulacion':'C','valorCuota':'0.00'}
        self.sid=payload['solicitudId'];self.intent=payload['intencionId']
        self.key=Ed25519PrivateKey.generate()
        self.policy={'version':PERSONAL_PURPOSE,'enabled':True,'usuarioWorkflow':'CGTE',
            'actorTecnico':'SYNTHETIC-SERVICE','solicitudes':[self.sid],'startEnabled':True}
        proof={'solicitudId':self.sid,'titularReferencia':sha256(payload['titular']['numeroDocumento'].encode()).hexdigest(),
            'revisionReferencia':str(uuid4()),'decisionReferencia':str(uuid4()),'decisionSha256':'d'*64,
            'versionCondiciones':'e'*64,'simulacionSha256':'f'*64,'aceptacionSha256':'a'*64,
            'aceptacionAuditoriaId':12,'cuota':'90000','monto':'500000.00','tasaNominalNamv':'20.00',
            'plazoMeses':6,'periodicidad':'MENSUAL','autorizaStart':False,
            'clasificacionRegistrada':'REQUIERE_CONSEJO_ADMINISTRACION'}
        self.proof=proof
        self.source={'payload':payload,'referencias':metadata['preparacion']['referencias'],
            'evidencias':{'consentimiento':metadata['preparacion']['evidencias']['consentimiento']},
            'perfilCore':metadata['perfilCore'],'preflight':metadata['preflight'],'simulacionAceptada':proof}

    def body(self,action):
        body={'accion':action,'solicitudId':self.sid,'intencionId':self.intent,'requestId':str(uuid4())}
        if action=='preparar':body['expediente']=deepcopy(self.source)
        if action=='confirmar':
            body['confirmacionPersonal']={k:self.proof[k] for k in ('aceptacionAuditoriaId','aceptacionSha256',
                'simulacionSha256','revisionReferencia','decisionReferencia','decisionSha256','versionCondiciones')}
            body['confirmacionPersonal'].update(auditoriaId=13,sha256='b'*64,sesionReferencia=str(uuid4()),
                confirmadaEn=NOW.isoformat(),sesionVenceEn=(NOW+timedelta(minutes=10)).isoformat(),aceptaRadicar=True)
        return body

    def sign(self,body,**changes):
        claim={'version':PERSONAL_PURPOSE,'audiencia':'CD6','accion':body['accion'],'solicitudId':self.sid,
            'intencionId':self.intent,'requestId':body['requestId'],'titularSha256':self.proof['titularReferencia'],
            'payloadSha256':sha256(canonical(body).encode()).hexdigest(),'emitidaEn':NOW.isoformat(),
            'venceEn':(NOW+timedelta(seconds=120)).isoformat(),'actorTecnico':'SYNTHETIC-SERVICE',
            'autorizaStart':body['accion']=='confirmar','autorizaFirma':False,'autorizaDesembolso':False,**changes}
        return {'algoritmo':'Ed25519','payload':claim,
            'firmaBase64':base64.b64encode(self.key.sign(PERSONAL_DOMAIN+canonical(claim).encode())).decode()}

    def test_capability_is_body_holder_case_purpose_and_expiry_bound(self):
        body=self.body('confirmar');signed=self.sign(body)
        valid=lambda e,b,**kw:validar_delegacion_personal(e,b,self.key.public_key(),self.policy,kw.get('now',NOW))
        self.assertTrue(valid(signed,body)['autorizaStart'])
        for bad in ({**body,'requestId':str(uuid4())},{**body,'solicitudId':str(uuid4())},
                {**body,'confirmacionPersonal':{**body['confirmacionPersonal'],'aceptaRadicar':False}}):
            with self.assertRaises(FichaError):valid(signed,bad)
        for change in ({'audiencia':'ADMINISTRATIVO'},{'autorizaDesembolso':True},{'autorizaFirma':True},
                {'actorTecnico':'ANOTHER-SERVICE'},{'autorizaStart':False}):
            with self.assertRaises(FichaError):valid(self.sign(body,**change),body)
        with self.assertRaises(FichaError):valid(signed,body,now=NOW+timedelta(minutes=3))
        with self.assertRaises(FichaError):validar_delegacion_personal(signed,body,Ed25519PrivateKey.generate().public_key(),self.policy,NOW)

    def test_start_requires_distinct_live_personal_confirmation_not_quote_acceptance_only(self):
        body=self.body('confirmar')
        for patch_values in ({'auditoriaId':None},{'aceptaRadicar':False},
                {'confirmadaEn':(NOW-timedelta(minutes=6)).isoformat()},
                {'sesionVenceEn':NOW.isoformat()}):
            bad=deepcopy(body);bad['confirmacionPersonal'].update(patch_values)
            with self.assertRaises(FichaError):
                validar_delegacion_personal(self.sign(bad),bad,self.key.public_key(),self.policy,NOW)

    def test_preparation_preserves_quota_and_explicit_113_semantics_without_admin_grant(self):
        body=self.body('preparar');claim=self.sign(body)['payload']
        prepared=prepare_personal(body['expediente'],claim)
        xml=prepared['preparacion']['xml']
        self.assertIn('<V_VALOR_CUOTA_CAL>90000.00</V_VALOR_CUOTA_CAL>',xml)
        self.assertIn('<V_VALOR_CUOTA>0.00</V_VALOR_CUOTA>',xml)
        self.assertNotIn('grantAdministrativo',prepared)
        for field,value in [('cuota','90001'),('monto','500001'),('aceptacionAuditoriaId',None),('autorizaStart',True)]:
            bad=deepcopy(self.source);bad['simulacionAceptada'][field]=value
            with self.assertRaises(ValueError):prepare_personal(bad,claim)
        bad=deepcopy(self.source);bad['payload']['contextoLinix'].pop('entrada113')
        with self.assertRaisesRegex(ValueError,'SEMANTICA_ENTRADA_113'):prepare_personal(bad,claim)

    def test_prepare_is_idempotent_and_never_starts_or_changes_other_intent(self):
        with TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'native.sqlite3',b'synthetic-private-secret',request_id=self.sid)
            try:
                body=self.body('preparar');args=(body,self.sign(body),journal,None,self.policy,self.key.public_key())
                first=operate_personal(*args,now=NOW);second=operate_personal(*args,now=NOW)
                self.assertEqual(first,second);self.assertFalse(first['STARTIntentado'])
                bad=deepcopy(body);bad['expediente']['payload']['intencionId']=str(uuid4())
                with self.assertRaises((ValueError,FichaError)):
                    operate_personal(bad,self.sign(bad),journal,None,self.policy,self.key.public_key(),now=NOW)
                self.assertEqual(journal.operation().intent_id,self.intent)
            finally:journal.close()

    def test_readonly_open_never_creates_journal_or_allows_writes(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'native.sqlite3'
            with self.assertRaises(FichaError):Journal(path,b'synthetic-private-secret',request_id=self.sid,create=False,read_only=True)
            self.assertFalse(path.exists())
            journal=Journal(path,b'synthetic-private-secret',request_id=self.sid);journal.close()
            original=path.read_bytes()
            reader=Journal(path,b'synthetic-private-secret',request_id=self.sid,create=False,read_only=True)
            try:
                self.assertEqual(reader.status()['estado'],'SIN_INTENCION')
                with self.assertRaises(Exception):reader.record('FORBIDDEN',{})
            finally:reader.close()
            self.assertEqual(path.read_bytes(),original)

    def test_personal_confirmation_uses_native_start_guard_once_with_fake_oracle(self):
        self._native_confirmation_roundtrip()

    def test_lost_start_response_reconciles_existing_sc_without_second_start(self):
        self._native_confirmation_roundtrip(response_lost=True)

    def _native_confirmation_roundtrip(self,*,response_lost=False):
        source=deepcopy(self.source)
        source['preflight']['configuracion'].update(rutasDerivadas=[],rutasDerivadasIncompletas=False)
        source['preflight']['sha256']=sha256(canonical(source['preflight']['configuracion']).encode()).hexdigest()
        fake=CasePort(source['payload'])
        fake.timeout=response_lost;fake.internal_commit=response_lost
        port=Port(personal_case=True)
        port.number_type=fake.number_type;port.clob_type=fake.clob_type
        errors=[]
        from .worker import safe_error
        def capture(error):
            import traceback
            errors.append((type(error).__name__,str(error),[(p.name,p.lineno) for p in traceback.extract_tb(error.__traceback__)[-4:]]))
            return safe_error(error)
        with TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'native.sqlite3',b'synthetic-private-secret',request_id=self.sid)
            try:
                prepare=self.body('preparar');prepare['expediente']=source
                operate_personal(prepare,self.sign(prepare),journal,port,self.policy,self.key.public_key(),now=NOW)
                confirm=self.body('confirmar')
                import json
                confirmation=confirm['confirmacionPersonal']
                confirmation['sha256']=sha256(json.dumps({'solicitudId':self.sid,'intencionId':self.intent,
                    'fingerprint':journal.operation().fingerprint,
                    **{k:v for k,v in confirmation.items() if k not in {'sha256','auditoriaId'}}},
                    sort_keys=True,default=str).encode()).hexdigest()
                with patch.object(port,'open_connection',side_effect=fake.open_connection),\
                        patch('tools.cd6.grant.perfil_personal',return_value=(self.policy,self.key.public_key())),\
                        patch('tools.cd6.worker.safe_error',side_effect=capture):
                    result=operate_personal(confirm,self.sign(confirm),journal,port,self.policy,self.key.public_key(),now=NOW)
                    self.assertTrue(result['STARTIntentado'],(result,errors))
                    self.assertIsNotNone(result['K_FLUJO'],result)
                    self.assertEqual(result['estado'],'RADICADA',(result,errors))
                    self.assertEqual(result['resultado']['activity_current'],6964)
                    self.assertEqual(fake.backend.executions,0)
                    original_flow=result['K_FLUJO']
                    repeated=operate_personal(confirm,self.sign(confirm),journal,port,self.policy,self.key.public_key(),now=NOW)
                    self.assertEqual(repeated['K_FLUJO'],original_flow)
                    self.assertEqual(journal.db.execute('SELECT COUNT(*) FROM cd6_start_guard').fetchone()[0],1)
                    from incluirtec.digital_operations.credihoy_oracle import START_SQL
                    self.assertEqual(len([s for s in fake.statements if s[0]==START_SQL]),1)
            finally:journal.close()

    def test_boundary_rejects_automatic_approval_unknown_action_or_branch_before_human(self):
        config=configuration();observed=lambda c:{'configuracion':c,'sha256':sha256(canonical(c).encode()).hexdigest()}
        result=require_personal_start_boundary(observed(config))
        self.assertEqual(result['paradasHumanas'][0]['actividad'],6964)
        for activity,action in [(6774,'PK_CA_WF_SOLICITUD_CREDITO.PR_APRUEBA_SOLICITUD'),(6096,'OTHER')]:
            bad=deepcopy(config);bad['ruta'].insert(2,{**bad['ruta'][1],'K_SERIE':999,'Q_TRANSI':3,'K_IDACTI':activity,'N_ACCION':action})
            with self.assertRaises(ValueError):require_personal_start_boundary(observed(bad))
        bad=deepcopy(config);bad['caminos']=[{'K_SERIE':7501,'K_IDRUTA_CAM':9999,'K_IDACTI_CAM':None}]
        with self.assertRaises(ValueError):require_personal_start_boundary(observed(bad))
