import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.historial_pago.extractor import extract_financial_metrics


class Command(BaseCommand):
    help = "Valida un XML HC2 y muestra las metricas normalizadas que usa Consumo."

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Ruta del XML HC2 a validar.')
        parser.add_argument(
            '--selected-key',
            action='append',
            default=[],
            dest='selected_keys',
            help='Llave de obligacion elegible a aplicar en pasivos que recoge. Repetible.',
        )

    def handle(self, *args, **options):
        xml_path = Path(options['file']).expanduser().resolve()
        if not xml_path.exists():
            raise CommandError(f'No existe el archivo: {xml_path}')

        payload = xml_path.read_text(encoding='utf-8', errors='ignore')
        result = extract_financial_metrics(payload, selected_keys=options['selected_keys'])
        metrics = result.get('metrics', {})
        response = {
            'file': str(xml_path),
            'metrics': metrics,
            'metrics_formatted': result.get('metrics_formatted', {}),
            'principal': {
                'saldo_total_creditos_deudor_principal': metrics.get('saldo_total_creditos_deudor_principal', 0),
                'total_cuotas_credito_deudor_principal': metrics.get('total_cuotas_credito_deudor_principal', 0),
            },
            'codeudor': {
                'saldo_abierto_codeudor': metrics.get('saldo_abierto_codeudor', 0),
                'cuota_abierta_codeudor': metrics.get('cuota_abierta_codeudor', 0),
            },
            'selected_keys_applied': result.get('selected_keys_applied', []),
            'obligaciones_elegibles': [
                {
                    'key': row.get('key'),
                    'entidad': row.get('entidad'),
                    'tipo_cuenta': row.get('tipo_cuenta'),
                    'numero_cuenta': row.get('numero_cuenta'),
                    'rol': row.get('rol'),
                    'estado_detalle': row.get('estado_detalle'),
                    'saldo_actual': row.get('saldo_actual'),
                    'valor_cuota': row.get('valor_cuota'),
                }
                for row in result.get('obligaciones_abiertas', [])
                if row.get('elegible_recoge')
            ],
        }
        self.stdout.write(json.dumps(response, ensure_ascii=False, indent=2))
