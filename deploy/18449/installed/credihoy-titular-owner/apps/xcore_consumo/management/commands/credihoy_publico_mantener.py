"""Revoke an access or clear expired identity ciphertext; never delete cases."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from apps.xcore_consumo.services.credihoy_v2.unico import publico_controles as controls


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument('--revocar', type=str)
        action.add_argument('--purgar', action='store_true')
        parser.add_argument('--operador', required=True)
        parser.add_argument('--referencia', required=True)
        parser.add_argument('--aplicar', action='store_true')

    def handle(self, *args, **opts):
        user = get_user_model().objects.filter(username=opts['operador'], is_active=True).first()
        if not user or not user.has_perm('xcore_consumo.revocar_accesopublicocredihoy'):
            raise CommandError('OPERADOR_SIN_PERMISO_REVOCACION')
        if not opts['referencia'].strip() or len(opts['referencia']) > 120:
            raise CommandError('REFERENCIA_AUDITABLE_REQUERIDA')
        if not opts['aplicar']:
            self.stdout.write('SOLO_LECTURA temporales_vencidos=' + str(controls.temporales_vencidos().count()))
            return
        try:
            kw = {'actor': str(user.pk), 'referencia': opts['referencia']}
            count = (int(controls.revocar(opts['revocar'], **kw)) if opts['revocar']
                else controls.purgar_temporales(**kw))
        except (ValueError, controls.Acceso.DoesNotExist):
            raise CommandError('ACCESO_NO_IDENTIFICADO') from None
        self.stdout.write('ACCESOS_INVALIDADOS=' + str(count) + ' EXPEDIENTES_ELIMINADOS=0')
