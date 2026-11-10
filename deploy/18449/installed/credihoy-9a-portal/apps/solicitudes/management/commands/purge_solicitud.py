from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.solicitudes.models import Solicitud
from apps.usuarios.models import Solicitante


class Command(BaseCommand):
    help = "Elimina solicitudes de prueba y sus relaciones por id, numero_solicitud o identificacion."

    def add_arguments(self, parser):
        parser.add_argument("--solicitud-id", dest="solicitud_id")
        parser.add_argument("--numero-solicitud", dest="numero_solicitud")
        parser.add_argument("--identificacion", dest="identificacion")
        parser.add_argument(
            "--delete-orphan-applicant",
            action="store_true",
            dest="delete_orphan_applicant",
            help="Elimina tambien el solicitante si queda sin solicitudes asociadas.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        solicitud_id = options.get("solicitud_id")
        numero_solicitud = options.get("numero_solicitud")
        identificacion = options.get("identificacion")

        if not any([solicitud_id, numero_solicitud, identificacion]):
            raise CommandError(
                "Debes indicar --solicitud-id, --numero-solicitud o --identificacion."
            )

        queryset = Solicitud.objects.select_related("solicitante")
        if solicitud_id:
            queryset = queryset.filter(id=solicitud_id)
        if numero_solicitud:
            queryset = queryset.filter(numero_solicitud=numero_solicitud)
        if identificacion:
            queryset = queryset.filter(solicitante__numero_identificacion=identificacion)

        solicitudes = list(queryset)
        if not solicitudes:
            raise CommandError("No se encontraron solicitudes con los criterios suministrados.")

        affected_numbers = [item.numero_solicitud for item in solicitudes]
        affected_applicants = {
            item.solicitante_id: item.solicitante for item in solicitudes if item.solicitante_id
        }
        count = len(solicitudes)

        for solicitud in solicitudes:
            solicitud.delete()

        orphan_removed = 0
        if options.get("delete_orphan_applicant"):
            for applicant_id, applicant in affected_applicants.items():
                if not Solicitud.objects.filter(solicitante_id=applicant_id).exists():
                    applicant.delete()
                    orphan_removed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Solicitudes eliminadas: {count} ({', '.join(affected_numbers)}). "
                f"Solicitantes huerfanos eliminados: {orphan_removed}."
            )
        )
