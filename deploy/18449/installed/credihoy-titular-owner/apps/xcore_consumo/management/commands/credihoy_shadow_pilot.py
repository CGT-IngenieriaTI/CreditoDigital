"""Administracion puntual. Por defecto solo preflight; nunca HTTP ni motor local."""

import json
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from apps.xcore_consumo.services.credihoy_shadow.piloto import (
    autorizar_y_encolar, preflight, reporte_piloto,
)


class Command(BaseCommand):
    help = "Prepara un piloto shadow puntual; requiere --autorizar-ejecucion para publicar en Celery."

    def add_arguments(self, parser):
        parser.add_argument("--solicitud", required=True, type=UUID)
        parser.add_argument("--request-id")
        parser.add_argument("--tipo-piloto", choices=("PILOTO_COMPARATIVO", "PILOTO_TECNICO"), default="PILOTO_COMPARATIVO")
        modo = parser.add_mutually_exclusive_group()
        modo.add_argument("--autorizar-ejecucion", action="store_true")
        modo.add_argument("--reporte", action="store_true")

    def handle(self, *args, **options):
        solicitud_id = str(options["solicitud"])
        request_id = options.get("request_id")
        try:
            if options["reporte"]:
                if not request_id:
                    raise CommandError("--reporte requiere --request-id")
                resultado = reporte_piloto(solicitud_id, request_id)
            else:
                resultado = preflight(solicitud_id, tipo_piloto=options["tipo_piloto"])
                if resultado["estado"] != "PILOTO_APTO":
                    raise CommandError("PILOTO_NO_APTO: " + ", ".join(resultado["errores"]))
                if options["autorizar_ejecucion"]:
                    resultado = autorizar_y_encolar(resultado, request_id)
                    if resultado["estado"] not in {"ENCOLADO", "DUPLICADO"}:
                        self.stdout.write(json.dumps(resultado, ensure_ascii=True))
                        raise CommandError("PILOTO_NO_ENCOLADO_O_SIN_AUDITORIA")
                else:
                    resultado["ejecucion_autorizada"] = False
            self.stdout.write(json.dumps(resultado, ensure_ascii=True, indent=2))
        except ValueError as exc:
            raise CommandError(str(exc)) from None
