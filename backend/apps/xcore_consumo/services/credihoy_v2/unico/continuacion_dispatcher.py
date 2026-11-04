"""One authorized dossier, existing PostgreSQL outbox, no public trigger."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from uuid import UUID


def configuration(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink():raise ValueError('ALCANCE_NO_PRIVADO')
    with path.open('rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size>4096
                or os.name!='nt' and (info.st_uid!=os.geteuid() or info.st_mode&0o077)):
            raise ValueError('ALCANCE_NO_PRIVADO')
        value=json.load(stream)
    if set(value)!={'version','solicitudId','operadorId','hasta','database','referencia'} or value['version']!=1:
        raise ValueError('ALCANCE_CONTRATO_INVALIDO')
    if str(UUID(value['solicitudId']))!=value['solicitudId'] or type(value['operadorId']) is not int:
        raise ValueError('ALCANCE_IDENTIFICADOR_INVALIDO')
    until=datetime.fromisoformat(value['hasta'])
    now=datetime.now(timezone.utc)
    if until.utcoffset() is None or not now<until<=now+timedelta(hours=1):raise ValueError('ALCANCE_VENCIDO')
    if not isinstance(value['referencia'],str) or not value['referencia'].strip():raise ValueError('ALCANCE_SIN_REFERENCIA')
    return value


def run(path,*,recover=False,interval=3):
    from django.conf import settings
    from django.db import connection
    from apps.xcore_consumo import tasks
    from apps.utils.models import AuditLog
    from .continuacion_eventos import EVENTO,FINAL,recuperar_individual

    scope=configuration(path)
    sid=scope['solicitudId']
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(),inet_server_addr()::text,inet_server_port(),current_schema()')
        if list(cursor.fetchone())!=scope['database']:raise ValueError('OUTBOX_BASE_DATOS_DIFIERE')
    # This settings object belongs only to the dedicated child process. No web
    # process or global financial gate is changed by this scoped dispatcher.
    settings.CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=(sid,)
    settings.CREDIHOY_CONTINUACION_OPERADOR_ID=scope['operadorId']
    settings.CREDIHOY_CONTINUACION_TRANSPORTE='POSTGRES_OUTBOX_INDIVIDUAL'
    if recover:recuperar_individual(sid)
    while True:
        current=configuration(path)
        if current!=scope:raise ValueError('ALCANCE_CAMBIO_REQUIERE_CONCILIACION')
        connection.close_if_unusable_or_obsolete()
        intent=AuditLog.objects.using('default').filter(solicitud_id=sid,event=EVENTO).order_by('-created_at','-pk').first()
        if intent is not None:
            try:
                result=tasks.continuar_credihoy_unico.run(evento_id=str(intent.request_id))
                done=AuditLog.objects.using('default').filter(solicitud_id=sid,event=FINAL,request_id=intent.request_id).exists()
                if done:
                    return {'estado':'EVENTO_INDIVIDUAL_CONSUMIDO','correlationId':str(intent.request_id),
                        'transporte':'POSTGRES_OUTBOX_INDIVIDUAL','resultado':result,
                        'HDCNuevas':0 if result['resultado'].get('evaluacionConservada') else None}
                if result.get('estado') not in {'EN_EJECUCION'}:
                    return {'estado':'TRANSICION_DETENIDA','correlationId':str(intent.request_id),'resultado':result}
            except Exception:
                print(json.dumps({'codigo':'CONTINUACION_REINTENTO_PENDIENTE','correlationId':str(intent.request_id)}),flush=True)
        time.sleep(interval)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--recuperar',action='store_true')
    args=parser.parse_args()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings')
    import django
    django.setup()
    try:
        print(json.dumps(run(args.config,recover=args.recuperar)),flush=True)
    except Exception:
        print(json.dumps({'codigo':'DESPACHADOR_DETENIDO_CONCILIAR'}),flush=True)
        raise SystemExit(1)
