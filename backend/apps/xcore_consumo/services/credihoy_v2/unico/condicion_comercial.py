"""Seleccion automatica desde hechos api_data. Solo lee catalogo; no cotiza."""




from django.conf import settings
from ..pagaduria import PagaduriaLinix
from apps.xcore_consumo.tasas_models import AsignacionTarifaCredihoy, TasaComercialCredihoy
from .tasas import hoy, vigente, comparar_tarifa, condiciones_aprobadas


def resolver_condicion(pagaduria, tasa_linix=None, *, corte=None, contexto_certificado=False):
    result = {'estado': 'TARIFA_NO_DETERMINADA', 'linea': None, 'lineaOrigen': None, 'tasaComercialNamv': None, 'tasaLinixNamv': tasa_linix, 'diferenciaPuntos': None, 'contratoConcordante': False, 'referenciaComercial': None, 'motivos': ['PAGADURIA_NO_ACREDITADA']}
    if not isinstance(pagaduria, PagaduriaLinix) or pagaduria.estado != 'DISPONIBLE' or pagaduria.activa is not True or (pagaduria.tipo not in ('N', 'V')):
        return result
    result.update(lineaOrigen={'N': 'NOMINA', 'V': 'VENTANILLA'}[pagaduria.tipo], pagaduria={'codigo': pagaduria.codigo, 'tipo': pagaduria.tipo, 'activa': pagaduria.activa, 'fuente': pagaduria.fuente, 'fechaConsulta': pagaduria.fechaConsulta, 'requestId': pagaduria.requestId}, motivos=['ASOCIACION_COMERCIAL_NO_RATIFICADA'])
    if not getattr(settings, 'CREDIHOY_TASAS_ENABLED', False):
        return result
    corte = corte or hoy()
    assignments = AsignacionTarifaCredihoy.objects.filter(perfil__producto='390', perfil__modalidad='111', alcance='TIPO', tipo_nomina=pagaduria.tipo, medio_pago='').prefetch_related('eventos')
    matches = [v for v in assignments if vigente(v, corte)]
    if len(matches) != 1:
        return result
    assignment = matches[0]
    if assignment.linea != {'N': 'LIBRANZA', 'V': 'VENTANILLA'}[pagaduria.tipo]:
        return {**result, 'motivos': ['ASIGNACION_NO_CORRESPONDE_A_TIPO_NOMINA']}
    documental = condiciones_aprobadas(assignment)
    result.update(asignacionId=assignment.pk, versionAsignacion=assignment.numero_version, referenciaAsignacion=documental['referencia'] if documental else assignment.referencia, soporteAsignacionSha256=documental['soporteSha256'] if documental else assignment.soporte_sha256, linea=assignment.linea)
    rates = TasaComercialCredihoy.objects.filter(perfil=assignment.perfil, linea=assignment.linea).prefetch_related('eventos')
    selected = [v for v in rates if vigente(v, corte)]
    if len(selected) != 1:
        return {**result, 'motivos': ['TARIFA_VIGENTE_UNIVOCA_AUSENTE']}
    return comparar_tarifa(result, selected[0], tasa_linix, contexto_certificado)


def pagaduria_seleccion(selected):
    """Validate the immutable chain without relabeling an old LINIX read as new."""
    import hashlib
    import json
    from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
    from ..pagaduria import adaptar_pagaduria
    digest=hashlib.sha256(json.dumps(selected.evidencia,sort_keys=True).encode()).hexdigest()
    if digest!=selected.evidencia_sha256:raise ValueError('EVIDENCIA_COMERCIAL_INVALIDA')
    recovery=selected.resultado.get('revalidacion')
    if recovery is None:
        return adaptar_pagaduria(selected.evidencia,request_id=str(selected.request_id),
            fecha_consulta=selected.evidencia['fechaConsulta'])
    try:
        prior=SeleccionTarifaCredihoy.objects.get(solicitud_id=selected.solicitud_id,
            pk=recovery['seleccionAnteriorId'],request_id=recovery['requestIdAnterior'])
    except SeleccionTarifaCredihoy.DoesNotExist:
        raise ValueError('EVIDENCIA_COMERCIAL_INVALIDA') from None
    if (prior.pk>=selected.pk or prior.evidencia_sha256!=digest or prior.evidencia!=selected.evidencia
            or recovery['evidenciaAnteriorSha256']!=digest or recovery['soloCotizacion'] is not True
            or recovery['aceptacionAsociadoInferida'] is not False):
        raise ValueError('EVIDENCIA_COMERCIAL_INVALIDA')
    return pagaduria_seleccion(prior)


def revalidar_seleccion_conservada(solicitud_id, operador, *, request_id, seleccion_anterior_request_id):
    """Explicit catalog recovery; preserve evidence date, prior selection and consent.

    This selects conditions for quotation only. A different previously selected
    rate requires review, never automatic replacement or inferred acceptance.
    """
    import hashlib
    import json
    from decimal import Decimal, InvalidOperation
    from uuid import UUID
    from django.core.exceptions import PermissionDenied, ValidationError
    from django.db import transaction
    from apps.solicitudes.models import Solicitud
    from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
    from apps.utils.models import AuditLog
    from ..pagaduria import adaptar_pagaduria
    from .consulta_controlada import PRODUCTO
    from .funcional import _firma
    key=UUID(str(request_id)); previous_key=UUID(str(seleccion_anterior_request_id))
    if key==previous_key:raise ValidationError('CLAVE_IDEMPOTENTE_INCOMPATIBLE')
    if not operador.is_authenticated or not operador.is_active or not operador.is_staff:raise PermissionDenied
    with transaction.atomic():
        s=Solicitud.objects.select_for_update().get(pk=solicitud_id,asesor=operador,producto=PRODUCTO)
        _firma(s)
        previous=SeleccionTarifaCredihoy.objects.get(solicitud=s,request_id=previous_key)
        digest=hashlib.sha256(json.dumps(previous.evidencia,sort_keys=True).encode()).hexdigest()
        if digest!=previous.evidencia_sha256:raise ValidationError('EVIDENCIA_COMERCIAL_INVALIDA')
        p=pagaduria_seleccion(previous)
        current=resolver_condicion(p)
        keys=('tarifaId','versionTarifa','asignacionId','versionAsignacion',
            'soporteTarifaSha256','soporteAsignacionSha256','tasaComercialNamv')
        if any(current.get(k) is None for k in keys):raise ValidationError('CONDICIONES_COMERCIALES_NO_VIGENTES')
        old_rate=previous.resultado.get('tasaComercialNamv')
        if old_rate is not None:
            try:
                a,b=Decimal(old_rate),Decimal(current['tasaComercialNamv'])
                same=a.is_finite() and b.is_finite() and a==b
            except (InvalidOperation,TypeError,ValueError):same=False
            if not same:raise ValidationError('CAMBIO_TASA_REQUIERE_REVISION_EXPLICITA')
        recovery={'seleccionAnteriorId':previous.pk,'requestIdAnterior':str(previous.request_id),
            'evidenciaAnteriorSha256':digest,'soloCotizacion':True,'aceptacionAsociadoInferida':False}
        existing=SeleccionTarifaCredihoy.objects.filter(request_id=key).first()
        if existing:
            if (existing.solicitud_id!=s.pk or existing.responsable_id!=operador.pk
                    or existing.resultado.get('revalidacion')!=recovery):
                raise ValidationError('CLAVE_IDEMPOTENTE_INCOMPATIBLE')
            return existing
        latest=SeleccionTarifaCredihoy.objects.filter(solicitud=s).order_by('-creado_at','-pk').first()
        if latest.pk!=previous.pk:raise ValidationError('SELECCION_COMERCIAL_CAMBIO')
        # Evidence requestId remains the original LINIX read, not this local recovery.
        selected=SeleccionTarifaCredihoy.objects.create(solicitud=s,responsable=operador,request_id=key,
            evidencia=previous.evidencia,evidencia_sha256=digest,resultado={**current,'revalidacion':recovery})
        AuditLog.objects.create(solicitud=s,actor=f'user:{operador.pk}',event='CREDIHOY_CONDICIONES_REVALIDADAS',
            request_id=str(key),payload={**recovery,'seleccionId':selected.pk,
                **{k:current[k] for k in keys},'Oracle':False,'START':0})
        return selected


def resolver_para_solicitud(solicitud_id, operador, *, request_id, cliente=None, autogestion=False):
    """Entrada institucional explicita, nunca ejecutada por GET de la pantalla.

    Reutiliza el cliente V2 autenticado y el control de pertenencia. La consulta
    es de hechos internos; no llama HDC, OTP, scoring ni transporte LINIX.
    """
    from dataclasses import asdict
    import hashlib
    import json
    from uuid import UUID
    from django.core.exceptions import PermissionDenied, ValidationError
    from django.db import transaction
    from django.utils import timezone
    from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
    from ..cliente import ApiDataCrediHoyV2Client, ConfiguracionApiDataV2
    from apps.solicitudes.models import Solicitud
    from .consulta_controlada import PRODUCTO
    from .estado_credito import _simulacion
    if not getattr(settings,'CREDIHOY_TASAS_ENABLED',False):raise ValidationError('CATALOGO_DESHABILITADO')
    if not operador.is_authenticated or not operador.is_active or not operador.is_staff:raise PermissionDenied
    key=UUID(str(request_id))
    with transaction.atomic():
        if autogestion:
            from .funcional import obtener_para_evaluacion,_firma
            s=obtener_para_evaluacion(solicitud_id,operador,autogestion=True,lock=True)
            _firma(s)
        else:
            s=Solicitud.objects.select_for_update().select_related('solicitante').get(
                pk=solicitud_id,asesor=operador,producto=PRODUCTO)
        previous=SeleccionTarifaCredihoy.objects.filter(request_id=key).first()
        if previous:
            if previous.solicitud_id!=s.pk or previous.responsable_id!=operador.pk:
                raise ValidationError('CLAVE_IDEMPOTENTE_INCOMPATIBLE')
            return previous
        client=cliente or ApiDataCrediHoyV2Client(ConfiguracionApiDataV2.desde_settings())
        now=timezone.now().replace(microsecond=0)
        facts=client.consultar(numero_documento=s.solicitante.numero_identificacion,
            fecha_evaluacion=now,request_id=str(key))
        p=facts.pagaduria
        if (p is not None and p.requestId!=str(key)) or facts.metadatos is None or facts.metadatos.request_id!=str(key):
            raise ValidationError('HECHOS_NO_VINCULADOS_A_SOLICITUD')
        evidence=asdict(p) if p else None
        digest=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
        result=resolver_condicion(p,_simulacion(s).get('tasaNamvObservada'))
        return SeleccionTarifaCredihoy.objects.create(solicitud=s,request_id=key,responsable=operador,
            evidencia=evidence or {},evidencia_sha256=digest,resultado=result)


def resolver_desde_revision_conservada(solicitud_id, operador, *, revision_referencia, persistir=False):
    """Bind the current study's authentic payroll evidence to the existing catalog.

    No rate is edited, source timestamp renewed, quote sent or offer accepted.
    The original institutional request id remains the selection's evidence id.
    """
    import hashlib, json
    from datetime import datetime
    from uuid import UUID
    from django.db import transaction
    from django.core.exceptions import PermissionDenied
    from django.utils import timezone
    from apps.utils.models import AuditLog
    from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
    from apps.xcore_consumo.services.otp_crypto import decrypt_text
    from .funcional import obtener_para_evaluacion, leer_estado_funcional
    from ..pagaduria import adaptar_pagaduria
    if type(persistir) is not bool:
        raise ValueError('SELECCION_PERSISTENCIA_INVALIDA')
    if not operador.is_authenticated or not operador.is_active or (not operador.is_staff):
        raise PermissionDenied
    ref = str(UUID(str(revision_referencia)))
    with transaction.atomic():
        case = obtener_para_evaluacion(solicitud_id, operador, autogestion=True, revision_conservada=True, lock=persistir)
        state = leer_estado_funcional(case)
        if state.get('revisionEstudio', {}).get('versionReferencia') != ref:
            raise ValueError('SELECCION_REVISION_NO_VIGENTE')
        draft = AuditLog.objects.get(solicitud=case, event='CREDIHOY_ESTUDIO_REVISION_PREPARADA', request_id=ref)
        rows = [r for r in state['resultado']['reglas'] if r['codigo'] == 'UNICO_FILA_32']
        if len(rows) != 1:
            raise ValueError('SELECCION_FUENTE_INSTITUCIONAL_NO_UNIVOCA')
        source_ref = rows[0].get('evidencia', {}).get('existeCredihoy111Registrado', {}).get('referencia_evidencia', '')
        if source_ref.startswith('CICLO_VIDA_111_V1:'):
            source_ref = source_ref[len('CICLO_VIDA_111_V1:'):]
        source_ref = str(UUID(source_ref))
        entry = AuditLog.objects.get(solicitud=case, event='credihoy_hechos_continuacion_v1', request_id=source_ref)
        raw = decrypt_text(entry.payload['hechosCifrados'])
        facts = json.loads(raw)
        if entry.created_at > draft.created_at or entry.payload.get('requestId') != source_ref or hashlib.sha256(raw.encode()).hexdigest() != entry.payload['sha256']:
            raise ValueError('SELECCION_FUENTE_CUSTODIADA_NO_INTEGRA')
        evidence = facts.get('pagaduria')
        if type(evidence) is not dict:
            raise ValueError('PAGADURIA_CUSTODIADA_NO_DISPONIBLE')
        p = adaptar_pagaduria(evidence, request_id=source_ref, fecha_consulta=evidence.get('fechaConsulta'))
        if p.estado != 'DISPONIBLE' or p.activa is not True or p.tipo not in {'N', 'V'} or (datetime.fromisoformat(p.fechaConsulta) > timezone.now()):
            raise ValueError('PAGADURIA_CUSTODIADA_NO_ACREDITADA')
        if getattr(settings, 'CREDIHOY_TASAS_ENABLED', False) is not True:
            raise ValueError('CATALOGO_COMERCIAL_NO_HABILITADO_EN_EJECUTOR')
        result = resolver_condicion(p)
        keys = ('tarifaId', 'versionTarifa', 'asignacionId', 'versionAsignacion', 'soporteTarifaSha256', 'soporteAsignacionSha256', 'tasaComercialNamv')
        if any((result.get(k) is None for k in keys)):
            raise ValueError('SELECCION_CATALOGO_VIGENTE_NO_ACREDITADA')
        digest = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        existing = SeleccionTarifaCredihoy.objects.filter(request_id=source_ref).first()
        latest = SeleccionTarifaCredihoy.objects.filter(solicitud=case).order_by('-creado_at', '-pk').first()
        if latest is not None and (existing is None or latest.pk != existing.pk):
            raise ValueError('SELECCION_CONSERVADA_REQUIERE_CONCILIACION')
        if existing and (existing.solicitud_id != case.pk or existing.evidencia != evidence or existing.evidencia_sha256 != digest or any((existing.resultado.get(k) != result[k] for k in keys))):
            raise ValueError('SELECCION_CONSERVADA_REQUIERE_CONCILIACION')
        selected = existing
        if persistir and selected is None:
            selected = SeleccionTarifaCredihoy.objects.create(solicitud=case, request_id=source_ref, responsable=operador, evidencia=evidence, evidencia_sha256=digest, resultado=result)
            AuditLog.objects.create(solicitud=case, event='CREDIHOY_SELECCION_DESDE_CUSTODIA', actor='user:' + str(operador.pk), request_id=source_ref, payload={'revisionReferencia': ref, 'reciboId': entry.pk, 'reciboSha256': entry.payload['sha256'], 'seleccionId': selected.pk, 'evidenciaSha256': digest, 'Oracle': 0, 'START': 0, 'aceptacionInferida': False, 'tasaModificada': False})
        return {'estado': 'SELECCION_COMERCIAL_VINCULADA' if selected else 'SELECCION_COMERCIAL_PREPARADA', 'persistido': selected is not None, 'seleccionId': selected.pk if selected else None, 'reciboId': entry.pk, 'reciboSha256': entry.payload['sha256'], 'evidenciaSha256': digest, 'revisionReferencia': ref, 'requestIdOriginal': source_ref, 'aceptacionInferida': False, 'tasaModificada': False, 'Oracle': 0, 'HDC': 0, 'START': 0, 'firma': 0}
