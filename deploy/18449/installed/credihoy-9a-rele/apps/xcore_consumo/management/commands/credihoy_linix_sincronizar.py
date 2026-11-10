"""Importa una conciliacion institucional verificada, sin llamadas externas."""
import json
from pathlib import Path
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from apps.solicitudes.models import Solicitud
from apps.xcore_consumo.services.credihoy_v2.unico.linix_estado import sincronizar


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument('--archivo', type=Path)
        source.add_argument('--stdin', action='store_true')

    def handle(self, *args, **options):
        try:
            raw = sys.stdin.read(16385) if options['stdin'] else options['archivo'].read_text(encoding='utf-8')
            if len(raw) > 16384:
                raise ValueError('SNAPSHOT_EXCEDE_LIMITE')
            p = json.loads(raw)
            s = Solicitud.objects.get(pk=p['solicitudId'])
            operador = get_user_model().objects.get(pk=s.asesor_id, is_active=True, is_staff=True)
            r = sincronizar(p, operador)
        except Exception:
            raise CommandError('ESTADO_LINIX_NO_SINCRONIZADO_REVISAR_EVIDENCIA') from None
        self.stdout.write(json.dumps({k: r[k] for k in ('estado', 'kFlujo', 'numeroSc', 'startEjecutados')}))
