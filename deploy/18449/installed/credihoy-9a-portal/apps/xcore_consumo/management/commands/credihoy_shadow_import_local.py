import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.xcore_consumo.services.credihoy_shadow.importador import validar_archivos, persistir_local, exigir_local


class Command(BaseCommand):
    help = "Valida evidencia privada fuera de Git; solo --persistir escribe en DEBUG y SQLite local."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--preselecta", required=True)
        parser.add_argument("--hdc", required=True)
        parser.add_argument("--persistir", action="store_true")
        parser.add_argument("--fecha-expedicion", type=date.fromisoformat)

    def handle(self, *args, **options):
        try:
            if options["persistir"]:
                exigir_local()
            datos = validar_archivos(options["preselecta"], options["hdc"])
            resultado = persistir_local(datos, fecha_expedicion=options.get("fecha_expedicion")) if options["persistir"] else datos.resumen
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps(resultado, ensure_ascii=True, indent=2))
