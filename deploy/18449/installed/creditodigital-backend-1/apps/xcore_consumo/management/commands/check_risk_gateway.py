"""Diagnostico opt-in de readiness; no ejecuta checks de DB ni migraciones."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.integraciones.risk_gateway.clientes import RiskGatewayHealthClient
from apps.integraciones.risk_gateway.errores import GatewayError


class Command(BaseCommand):
    help = "Consulta solo Health del Risk Gateway, sin comprobar proveedores."
    requires_system_checks = []
    requires_migrations_checks = False

    def handle(self, *args, **options):
        try:
            resultado = RiskGatewayHealthClient().check()
        except GatewayError as error:
            raise CommandError(error.codigo) from None
        self.stdout.write(json.dumps(resultado.a_dict(), sort_keys=True))
