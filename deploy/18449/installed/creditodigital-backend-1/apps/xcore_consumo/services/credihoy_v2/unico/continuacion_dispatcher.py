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


def run_institutional(*, interval=15, once=False):
    """A supervised outbox consumer: one bounded event, never a dossier sweep."""
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db import connection
    from django.db.models import Exists,OuterRef
    from apps.utils.models import AuditLog
    from apps.integraciones.risk_gateway.operaciones.errores import OperacionError
    from .consulta_controlada import cargar_politica_consumo,autorizar_consumo_por_politica,PRODUCTO
    from .continuacion_eventos import EVENTO,FINAL
    from ..cliente import ConfiguracionApiDataV2
    from apps.xcore_consumo import tasks
    from django.test.utils import override_settings
    while True:
        try:
            policy=cargar_politica_consumo()
            ConfiguracionApiDataV2.desde_settings()
            username=getattr(settings,'CREDIHOY_CONTINUACION_OPERADOR_USERNAME','')
            operator=get_user_model().objects.get(username=username,is_active=True,is_staff=True,is_superuser=False)
            if operator.has_usable_password() or not operator.has_perm('xcore_consumo.add_evaluacionconsumo'):
                raise OperacionError('OPERADOR_SERVICIO_NO_AUTORIZADO')
            final=AuditLog.objects.filter(event=FINAL,request_id=OuterRef('request_id'),solicitud_id=OuterRef('solicitud_id'))
            from django.utils import timezone
            recent=AuditLog.objects.filter(event='credihoy_continuacion_intento_institucional_v1',
                created_at__gte=timezone.now()-timedelta(minutes=5)).values('request_id')
            intent=AuditLog.objects.filter(event=EVENTO,created_at__gte=policy.datos['eventosDesde'],solicitud__producto=PRODUCTO,
                solicitud__metadatos__modo='AUTOGESTION_ENVIADA').annotate(
                completed=Exists(final)).filter(completed=False).exclude(
                request_id__in=recent).order_by('created_at','pk').first()
            if intent is not None:
                sid=str(intent.solicitud_id)
                AuditLog.objects.create(solicitud_id=sid,event='credihoy_continuacion_intento_institucional_v1',
                    request_id=intent.request_id,actor='user:'+str(operator.pk),
                    payload={'politicaSha256':policy.huella(),'proveedorEjecutado':False})
                # Enabled only in this isolated process and only after a valid policy.
                with override_settings(CREDIHOY_EVALUACION_AUTOGESTION_ENABLED=True,
                        RISK_GATEWAY_ORCHESTRATION_ENABLED=True,
                        RISK_GATEWAY_POSTGRESQL_CONCURRENCY_CERTIFIED=True,
                        CREDIHOY_CONTINUACION_SOLICITUDES_AUTORIZADAS=(sid,),
                        CREDIHOY_CONTINUACION_OPERADOR_ID=operator.pk,
                        CREDIHOY_CONTINUACION_TRANSPORTE='POSTGRES_OUTBOX_INSTITUCIONAL'):
                    autorizar_consumo_por_politica(sid,operador=operator)
                    result=tasks.continuar_credihoy_unico.run(evento_id=str(intent.request_id))
                    print(json.dumps({'correlationId':str(intent.request_id),'estado':result['estado']}),flush=True)
        except OperacionError as exc:
            print(json.dumps({'codigo':exc.codigo,'consultaProveedor':False}),flush=True)
        except Exception:
            # No uncertain provider result is automatically retried by the gateway.
            print(json.dumps({'codigo':'CONTINUIDAD_INSTITUCIONAL_PENDIENTE_CONCILIACION'}),flush=True)
        finally:connection.close_if_unusable_or_obsolete()
        if once:return
        time.sleep(max(5,interval))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config');parser.add_argument('--recuperar',action='store_true');parser.add_argument('--institucional',action='store_true')
    args=parser.parse_args()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','core.settings')
    import django
    django.setup()
    try:
        if args.institucional:
            if args.config or args.recuperar:raise ValueError('ALCANCES_INCOMPATIBLES')
            run_institutional()
        else:print(json.dumps(run(args.config,recover=args.recuperar)),flush=True)
    except Exception:
        print(json.dumps({'codigo':'DESPACHADOR_DETENIDO_CONCILIAR'}),flush=True)
        raise SystemExit(1)
