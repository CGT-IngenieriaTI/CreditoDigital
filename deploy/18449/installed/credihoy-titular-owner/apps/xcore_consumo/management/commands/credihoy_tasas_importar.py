"""Importa solo borradores documentales; nunca aprueba ni activa condiciones."""
import json
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.core.exceptions import PermissionDenied
from apps.xcore_consumo.services.credihoy_v2.unico.tasas import administrar, permiso, estado
from apps.xcore_consumo.tasas_models import (
    PerfilTasasCredihoy, TasaComercialCredihoy, AsignacionTarifaCredihoy,
)


def importar_borrador(actor, perfil, row, *, asignacion=False):
    common = {'linea', 'desde', 'hasta', 'documento', 'versionDocumento',
              'referencia', 'soporteSha256', 'observacion'}
    extra = {'medioPago', 'tipoNomina'} if asignacion else {'ea', 'namv', 'mvPublicada'}
    if asignacion and isinstance(row, dict) and 'alcance' in row:
        extra.add('alcance')
    if type(row) is not dict or set(row) != common | extra:
        raise CommandError('CAMPOS_BORRADOR_INVALIDOS')
    names = {'versionDocumento': 'version_documento', 'soporteSha256': 'soporte_sha256',
        'ea': 'tasa_ea', 'namv': 'tasa_namv', 'mvPublicada': 'tasa_mv_publicada',
        'medioPago': 'medio_pago', 'tipoNomina': 'tipo_nomina'}
    fields = {names.get(k, k): v for k, v in row.items()}
    model = AsignacionTarifaCredihoy if asignacion else TasaComercialCredihoy
    if asignacion:
        fields.setdefault('alcance', 'MEDIO')
    elif any(not isinstance(row[k], str) for k in ('ea','namv','mvPublicada')):
        raise CommandError('PORCENTAJE_DECIMAL_EXPLICITO')
    candidate = model(perfil=perfil, responsable=actor, numero_version=1, **fields)
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    fields = {k: getattr(candidate, k) for k in fields}
    identity = {'perfil': perfil, 'linea': row['linea'], 'soporte_sha256': row['soporteSha256']}
    if asignacion:
        identity.update(alcance=fields['alcance'], medio_pago=fields['medio_pago'], tipo_nomina=fields['tipo_nomina'])
    candidates = model.objects.filter(**identity)
    if candidates.exists():
        exact = candidates.filter(**fields)
        if exact.count() != 1 or estado(exact.first()) != 'CREADA':
            raise CommandError('REGISTRO_EXISTENTE_DIFIERE_O_NO_ES_BORRADOR')
        obj = exact.get()
        return {'tipo': 'ASIGNACION' if asignacion else 'TASA', 'id': obj.pk,
                'estado': 'CREADA', 'reutilizado': True}
    if asignacion:
        suffix = (':'+row['tipoNomina'] if fields['alcance'] == 'TIPO' else
                  ':MEDIO:'+row['medioPago']+':'+row['tipoNomina'])
        namespace = 'credihoy-cd11-asociacion:' + row['soporteSha256'] + suffix
    else:
        namespace = 'credihoy-tarifa-documental:' + row['soporteSha256'] + ':' + row['linea']
    administrar(actor, {'accion': 'crear_asignacion' if asignacion else 'crear_tasa',
        'datos': row, 'referenciaAutorizacion': 'IMPORTACION_DOCUMENTAL_SIN_ACTIVACION'},
        uuid5(NAMESPACE_URL, namespace))
    obj = model.objects.get(**identity, **{k: v for k, v in fields.items() if k not in identity})
    return {'tipo': 'ASIGNACION' if asignacion else 'TASA', 'id': obj.pk,
            'estado': estado(obj), 'reutilizado': False}


@transaction.atomic
def importar_paquete(actor, packet):
    if getattr(settings,'CREDIHOY_TASAS_ENABLED',False) is not True:raise CommandError('CATALOGO_NO_HABILITADO')
    if type(packet) is not dict or packet.get('perfil')!=['390','111'] or packet.get('soloBorradores') is not True:
        raise CommandError('ALCANCE_INVALIDO')
    if set(packet) - {'perfil','soloBorradores','condiciones','asignaciones'}:
        raise CommandError('CAMPOS_PAQUETE_INVALIDOS')
    if not permiso(actor, 'administrar'):raise PermissionDenied
    conditions=packet.get('condiciones')
    assignments=packet.get('asignaciones', [])
    if (not isinstance(conditions,list) or not isinstance(assignments,list)
            or not conditions or len(conditions)+len(assignments)>10):raise CommandError('BORRADORES_INVALIDOS')
    perfil,_=PerfilTasasCredihoy.objects.get_or_create(producto='390',modalidad='111')
    PerfilTasasCredihoy.objects.select_for_update().get(pk=perfil.pk)
    records=[importar_borrador(actor,perfil,row) for row in conditions]
    records += [importar_borrador(actor,perfil,row,asignacion=True) for row in assignments]
    return {'borradores':len(conditions),'activaciones':0,
            'asignaciones':len(assignments),'registros':records}


class Command(BaseCommand):
    help='Registra borradores documentales de tarifas y asociaciones; nunca aprueba ni activa.'

    def add_arguments(self,parser):
        parser.add_argument('--archivo',required=True)
        parser.add_argument('--responsable',required=True)

    def handle(self,*args,**options):
        if getattr(settings,'CREDIHOY_TASAS_ENABLED',False) is not True:raise CommandError('CATALOGO_NO_HABILITADO')
        p=Path(options['archivo'])
        if not p.is_file() or p.stat().st_size>20000:raise CommandError('ARCHIVO_INVALIDO')
        packet=json.loads(p.read_text(encoding='utf-8'))
        actor=get_user_model().objects.get(username=options['responsable'])
        self.stdout.write(json.dumps(importar_paquete(actor,packet)))
