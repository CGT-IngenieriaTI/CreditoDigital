from django.core.management.base import BaseCommand, CommandError
from apps.xcore_consumo.services.credihoy_v2.unico.progreso_temporal import limpiar


class Command(BaseCommand):
    help = 'Elimina fisicamente progreso CrediHoy vencido; nunca expedientes enviados.'

    def add_arguments(self, parser):
        parser.add_argument('--lote',type=int,default=500)
        parser.add_argument('--max-lotes',type=int,default=20)

    def handle(self,*args,**options):
        if options['max_lotes']<1 or not 1<=options['lote']<=5000:
            raise CommandError('PARAMETROS_LIMPIEZA_INVALIDOS')
        total=0
        for _ in range(options['max_lotes']):
            try:count=limpiar(lote=options['lote'])
            except Exception:raise CommandError('LIMPIEZA_NO_COMPLETADA_REVISAR_SERVICIO_SIN_DATOS_PERSONALES') from None
            total+=count
            if count<options['lote']:
                self.stdout.write('PROGRESO_TEMPORAL_ELIMINADO='+str(total));return
        raise CommandError('LIMPIEZA_INCOMPLETA_REVISAR_PLANIFICACION_SIN_DATOS_PERSONALES')
