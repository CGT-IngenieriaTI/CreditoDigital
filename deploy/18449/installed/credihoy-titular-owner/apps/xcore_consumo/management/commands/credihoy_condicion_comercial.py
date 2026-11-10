"""Lectura institucional explicita. No dispara START ni altera la solicitud."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from apps.xcore_consumo.services.credihoy_v2.unico.condicion_comercial import resolver_para_solicitud


class Command(BaseCommand):
    help='Resuelve pagaduria via api_data V2 y conserva seleccion comercial separada.'

    def add_arguments(self, parser):
        parser.add_argument('--solicitud',required=True)
        parser.add_argument('--operador',required=True)
        parser.add_argument('--request-id',required=True)

    def handle(self,*args,**options):
        try:
            actor=get_user_model().objects.get(username=options['operador'])
            selection=resolver_para_solicitud(options['solicitud'],actor,request_id=options['request_id'])
        except Exception:
            raise CommandError('CONDICION_COMERCIAL_NO_CONFIRMADA_SIN_START') from None
        self.stdout.write(selection.resultado['estado'])
