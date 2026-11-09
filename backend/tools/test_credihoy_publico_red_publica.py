"""Synthetic public-boundary tests; no real identity, OTP or provider calls."""
from hashlib import sha256
from django.test import RequestFactory,SimpleTestCase,override_settings
from apps.xcore_consumo.services.credihoy_v2.unico import publico_controles as controls
from apps.xcore_consumo.services.credihoy_v2.unico import publico_privacidad as privacy


@override_settings(CREDIHOY_PUBLICO_RESTRINGIDO=False,CREDIHOY_PUBLICO_MODO='PUBLICO_HTTPS',
    CREDIHOY_PUBLICO_PROXIES=['10.0.13.7/32'],CREDIHOY_PUBLICO_REDES=[],
    CREDIHOY_PUBLICO_IDENTIDADES_HMAC=[],CREDIHOY_PUBLICO_AUTORIZACION_HASTA='2000-01-01T00:00:00Z',
    CREDIHOY_PUBLICO_HOST='consulta.congente.coop',ALLOWED_HOSTS=['consulta.congente.coop'],
    CSRF_TRUSTED_ORIGINS=['https://consulta.congente.coop:8047'],FORCE_SCRIPT_NAME='/credihoy',
    CREDIHOY_PUBLICO_AVISO_VERSION='SYNTHETIC_ONLY',CREDIHOY_PUBLICO_AVISO_TEXTO='SYNTHETIC NOTICE ONLY',
    CREDIHOY_PUBLICO_RETENCION_DIAS=30,CREDIHOY_PUBLICO_BOT_SITEKEY='synthetic-public-key-only',
    CREDIHOY_PUBLICO_BOT_SECRET='synthetic-private-secret-only')
class PublicNetworkTests(SimpleTestCase):
    @override_settings(CREDIHOY_PERFIL_DISPONIBILIDAD='PUBLICO_HTTPS',SETTINGS_MODULE='core.settings_credihoy_publico',
        CREDIHOY_PUBLICO_AUTORIZACION_HASTA='',CREDIHOY_PUBLICO_BORDE_BASIC_EXTERNO=False,
        CREDIHOY_IDENTIDAD_GRANTS_ENABLED=True,CREDIHOY_ADMISION_ENSAYO={},
        CSRF_TRUSTED_ORIGINS=['https://consulta.congente.coop:18449'],FORCE_SCRIPT_NAME=None)
    def test_continuous_18449_keeps_all_public_controls_without_global_deadline(self):
        controls.validar_habilitacion()
        self.assertEqual(controls.ip_cliente(self.request(HTTP_HOST='consulta.congente.coop:18449',
            HTTP_X_FORWARDED_PREFIX='')),'198.51.100.8')
        for change in ({'CREDIHOY_PUBLICO_AUTORIZACION_HASTA':'2099-01-01T00:00:00Z'},
                {'CREDIHOY_IDENTIDAD_GRANTS_ENABLED':False},{'CREDIHOY_PUBLICO_BORDE_BASIC_EXTERNO':True},
                {'CREDIHOY_PUBLICO_VALIDACION':{**self.approval,'estado':'BORRADOR'}},
                {'CREDIHOY_PUBLICO_BOT_SECRET':''},{'CREDIHOY_PUBLICO_IDENTIDADES_HMAC':['a'*64]},
                {'CREDIHOY_ADMISION_ENSAYO':{'version':'OLD_PILOT'}},
                {'SETTINGS_MODULE':'core.settings_credihoy_restringido'},
                {'CSRF_TRUSTED_ORIGINS':['https://consulta.congente.coop:8047']}):
            with self.subTest(fields=tuple(change)),override_settings(**change),self.assertRaises(controls.AccesoDenegado):
                controls.validar_habilitacion()

    def setUp(self):
        self.approval={'estado':'VALIDADO','referenciaPrivacidad':'SYNTHETIC_ONLY',
            'referenciaSeguridad':'SYNTHETIC_ONLY','politicaSha256':privacy.POLITICA['sha256'],
            'avisoVersion':'SYNTHETIC_ONLY','avisoSha256':sha256(b'SYNTHETIC NOTICE ONLY').hexdigest(),
            'sesionSegundos':900,'otpSegundos':300,'limpiezaTemporalMaxSegundos':60,'ventanaSeguimientoDias':30}
        context=override_settings(CREDIHOY_PUBLICO_VALIDACION=self.approval)
        context.enable();self.addCleanup(context.disable)

    def request(self,**overrides):
        headers={'REMOTE_ADDR':'10.0.13.7','HTTP_HOST':'consulta.congente.coop:8047',
            'HTTP_X_FORWARDED_FOR':'198.51.100.8','HTTP_X_FORWARDED_PROTO':'https',
            'HTTP_X_FORWARDED_PREFIX':'/credihoy'}
        headers.update(overrides)
        return RequestFactory().get('/api/v1/credihoy/publico/sesion/',secure=True,**headers)

    def test_public_admission_does_not_depend_on_old_window_cohort_or_cidr_catch_all(self):
        controls.validar_habilitacion()
        self.assertTrue(controls.identidad_permitida('a'*64))
        self.assertEqual(controls.ip_cliente(self.request()),'198.51.100.8')
        for identity in (None,True,'','wrong','A'*64):self.assertFalse(controls.identidad_permitida(identity))

    def test_unapproved_notice_or_missing_evidence_is_not_bypassed(self):
        for changes in ({'estado':'BORRADOR'},{'referenciaPrivacidad':''},{'referenciaSeguridad':''},
                {'politicaSha256':'0'*64},{'avisoSha256':'0'*64},{'ventanaSeguimientoDias':None}):
            with self.subTest(changes=changes),override_settings(CREDIHOY_PUBLICO_VALIDACION={**self.approval,**changes}),self.assertRaises(controls.AccesoDenegado):
                controls.validar_habilitacion()

    def test_empty_or_provider_dummy_antibot_keys_remain_closed(self):
        for name in ('CREDIHOY_PUBLICO_BOT_SITEKEY','CREDIHOY_PUBLICO_BOT_SECRET'):
            for value in ('',None,'1x00000000000000000000AA'):
                with self.subTest(name=name,value=value),override_settings(**{name:value}),self.assertRaises(controls.AccesoDenegado):
                    controls.validar_habilitacion()

    def test_public_mode_cannot_be_enabled_by_flipping_only_a_boolean(self):
        for mode in (None,'','ENSAYO_HTTPS_RESTRINGIDO'):
            with override_settings(CREDIHOY_PUBLICO_MODO=mode),self.assertRaises(controls.AccesoDenegado):controls.validar_habilitacion()

    def test_trusted_proxy_must_be_explicit_and_individual(self):
        for proxies in ([],['0.0.0.0/0'],['10.0.13.0/24'],['bad']):
            with self.subTest(proxies=proxies),override_settings(CREDIHOY_PUBLICO_PROXIES=proxies),self.assertRaises(controls.AccesoDenegado):
                controls.validar_habilitacion()

    def test_forged_proxy_headers_host_prefix_and_multicast_are_rejected(self):
        for changes in ({'REMOTE_ADDR':'10.0.13.8'},{'HTTP_X_FORWARDED_FOR':'198.51.100.1, 198.51.100.2'},
                {'HTTP_X_FORWARDED_PROTO':'http'},{'HTTP_X_REAL_IP':'198.51.100.1'},
                {'HTTP_FORWARDED':'for=198.51.100.1'},{'HTTP_HOST':'evil.invalid'},
                {'HTTP_HOST':'consulta.congente.coop:18449'},{'HTTP_X_FORWARDED_PREFIX':'/admin'},
                {'HTTP_X_FORWARDED_FOR':'0.0.0.0'},{'HTTP_X_FORWARDED_FOR':'224.0.0.1'}):
            with self.subTest(changes=changes),self.assertRaises(controls.AccesoDenegado):controls.ip_cliente(self.request(**changes))
