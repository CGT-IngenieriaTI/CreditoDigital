"""Concrete proposal, not a statement of institutional acceptance."""
from hashlib import sha256
from django.conf import settings
from django.core import signing
from django.utils import timezone
from . import publico_institucional as wire

VERSION = 'CREDIHOY_PROGRESO_60M_V3'
TEXTO = '''Congente utilizará los datos que ingreses para guardar temporalmente el avance de tu formulario de crédito y permitirte continuarlo después.

Si decides guardar tu progreso, podrás recuperarlo durante 60 minutos desde tu última actividad autenticada y autorizada. Las consultas automáticas no amplían ese plazo.

Cuando venza el tiempo, ya no podrás recuperar el progreso y la información temporal se eliminará de la base operativa mediante una limpieza programada.

Las copias de respaldo podrían conservar información cifrada durante un período adicional, bajo controles de acceso restringido. Por eso, el vencimiento del progreso no implica la eliminación inmediata de todas las copias.

Si tu sesión vence, tendrás que autenticarte nuevamente para recuperar un progreso que todavía esté vigente. Una falla técnica podría impedir su recuperación.

Si finalizas y registras tu solicitud, la información se conservará como expediente institucional, conforme a las reglas aplicables, y dejará de estar sujeta al plazo de 60 minutos del progreso temporal.

En esta etapa, registrar tu solicitud no significa radicarla en LINIX ni iniciar automáticamente una consulta a centrales de riesgo, aprobación, firma o desembolso. Esas operaciones tienen controles y autorizaciones independientes.

Puedes consultar la Política de Tratamiento de Datos Personales de Congente y ejercer tus derechos mediante los canales institucionales indicados en ella.

Al aceptar, autorizas el tratamiento descrito para guardar temporalmente tu progreso y, únicamente si decides finalizar y registrar tu solicitud, su conservación como expediente institucional.'''
SHA256 = sha256(TEXTO.encode()).hexdigest()
SALT = 'credihoy-presentacion-progreso-v1'


def configuracion():
    cfg = getattr(settings, 'CREDIHOY_PROGRESO_VALIDACION', {})
    if (type(cfg) is not dict or cfg.get('version') != VERSION or cfg.get('sha256') != SHA256
            or not cfg.get('consistenciaPoliticaReferencia') or not cfg.get('respaldoCustodiaReferencia')
            or type(cfg.get('limpiezaMaxSegundos')) is not int or not 1 <= cfg['limpiezaMaxSegundos'] <= 60):
        raise wire.NoDisponible
    return cfg


def presentar(identity):
    configuracion()
    token = signing.dumps({'sesion': identity['_sesionReferencia'], 'version': VERSION, 'sha256': SHA256},
        salt=SALT, key=settings.CREDIHOY_TITULAR_SECRET)
    return {'version': VERSION, 'sha256': SHA256, 'texto': TEXTO, 'presentacion': token,
        'inactividadSegundos': 3600}


def aceptar(identity, value):
    configuracion()
    if type(value) is not dict or set(value) != {'version','sha256','presentacion','acepta'} or value['acepta'] is not True:
        raise ValueError('ACEPTACION_TEMPORAL_REQUERIDA')
    proof = signing.loads(value['presentacion'], salt=SALT, key=settings.CREDIHOY_TITULAR_SECRET,
        max_age=3600, fallback_keys=[])
    if (proof != {'sesion': identity['_sesionReferencia'], 'version': VERSION, 'sha256': SHA256}
            or value['version'] != VERSION or value['sha256'] != SHA256):
        raise ValueError('PRESENTACION_TEMPORAL_INCOMPATIBLE')
    return {'version': VERSION, 'sha256': SHA256, 'accion': 'ACEPTO_GUARDAR_TEMPORALMENTE',
        'aceptadoEn': timezone.now().isoformat(), 'sesionReferencia': identity['_sesionReferencia']}
