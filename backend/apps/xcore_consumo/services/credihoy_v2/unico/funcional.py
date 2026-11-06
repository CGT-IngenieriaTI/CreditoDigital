"""Resultado funcional persistente UNICO; sin consultas ni decisiones financieras."""





















from decimal import Decimal
from dataclasses import replace
import hashlib
import hmac
import json
import re
from uuid import NAMESPACE_URL, uuid5

from django.db import transaction
from django.utils import timezone

from apps.utils.models import AuditLog
from apps.xcore_consumo.models import EvaluacionConsumo, SolicitudConsumo
from apps.xcore_consumo.services.consentimiento_integridad import build_consent_hash
from ..base import EstadoCertificacion as EC, Hecho
from .consentimiento_real import obtener, validar_documento
from .dto import EntradaCredihoyUnicoV2, RecogidaCredihoyUnicoV2
from .expediente import preparar_expediente_unico
from .motor import evaluar_capacidad_unico_v2
from .reglas import HechosReglasUnicoV2, evaluar_reglas_unico_v2


VERSION = "credihoy-resultado-funcional-v1"
KEY = "credihoy_unico_funcional"
KEY_HDC = "credihoy_unico_hdc_actual"
KEY_HDC_REVISION = "credihoy_unico_hdc_revision"
KEY_CONDICIONES = "credihoy_condiciones_revisiones"
ESTADOS_CAPACIDAD = {"CUMPLE_CAPACIDAD", "NO_CUMPLE_CAPACIDAD", "NO_EVALUABLE"}


class ConflictoFuncional(ValueError):
    pass


def obtener_para_evaluacion(solicitud_id, operador, *, autogestion=False, lock=False, revision_conservada=False):
    if revision_conservada and (not autogestion):
        raise ConflictoFuncional('REVISION_CONSERVADA_REQUIERE_AUTOGESTION')
    if not autogestion:
        return obtener(solicitud_id, operador, lock=lock)
    from django.conf import settings
    from apps.solicitudes.models import Solicitud
    from .autoservicio import validar_vinculo_consumo
    if not operador.is_authenticated or not operador.is_active or (not operador.is_staff) or (not operador.has_perm('xcore_consumo.add_evaluacionconsumo')):
        raise ConflictoFuncional('EVALUACION_AUTOGESTION_NO_HABILITADA')
    if not revision_conservada and getattr(settings, 'CREDIHOY_EVALUACION_AUTOGESTION_ENABLED', False) is not True:
        raise ConflictoFuncional('EVALUACION_AUTOGESTION_NO_HABILITADA')
    query = Solicitud.objects.select_related('solicitante')
    if lock:
        query = query.select_for_update(of=('self',))
    case = validar_vinculo_consumo(query.get(pk=solicitud_id, asesor__isnull=True, metadatos__modo='AUTOGESTION_ENVIADA'))
    if revision_conservada:
        from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
        evaluation = EvaluacionConsumo.objects.get(solicitud=case)
        if evaluation.input_snapshot.get('revisionHdcActiva'):
            raise ConflictoFuncional('REVISION_HISTORICA_NO_MODIFICABLE_POR_CONTINUIDAD')
        intent = AuditLog.objects.get(solicitud=case, event='credihoy_continuacion_solicitada_v1')
        AuditLog.objects.get(solicitud=case, event='credihoy_continuacion_evento_completado_v1', request_id=intent.request_id)
        if not AuditLog.objects.filter(solicitud=case, event='credihoy_cupo_hdc_reservado_v1').exists():
            raise ConflictoFuncional('REVISION_SIN_ALCANCE_INSTITUCIONAL')
        operation = OperacionGateway.objects.filter(pk=evaluation.input_snapshot[KEY_HDC]['operacionId'], solicitud=case, consentimiento=_firma(case), proveedor='HDC', estado='COMPLETADA').first()
        if operation is None:
            raise ConflictoFuncional('REVISION_SIN_HDC_CONSERVADA')
        _exigir_recibo_hdc(operation)
    return case


def _exigir_recibo_hdc(op):
    from apps.integraciones.risk_gateway.operaciones.servicio import _recibo
    if (not op.request_hmac or not op.recibo_hmac
            or not hmac.compare_digest(op.recibo_hmac,_recibo(op))):
        raise ConflictoFuncional('RECIBO_HDC_NO_INTEGRO')


def clasificar_capacidad(capacidad, *, umbral_mayor_que=None):
    """Condicion estricta suministrada y autorizada por contexto, nunca default 0.

    Este punto NO aprueba creditos; la habilitacion real sigue sin politica.
    Los tests suministran una politica inequivocamente sintetica.
    """
    h = umbral_mayor_que
    autorizado = (isinstance(h, Hecho) and h.disponible and type(h.valor) is Decimal
        and h.valor.is_finite() and h.valor >= 0 and h.estado_certificacion == EC.CERTIFICADO
        and bool(h.fuente and h.ruta_origen and h.referencia_evidencia))
    if not capacidad.capacidad_pago.disponible:
        return {"estado": "NO_EVALUABLE", "motivos": list(capacidad.operandos_faltantes)
                or list(capacidad.inconsistencias), "referenciaPolitica": None}
    if not autorizado:
        return {"estado": "NO_EVALUABLE", "motivos": ["CONDICION_CAPACIDAD_Y_EFECTOS_NO_AUTORIZADOS"],
                "referenciaPolitica": None}
    cumple = capacidad.capacidad_pago.valor > h.valor
    return {"estado": "CUMPLE_CAPACIDAD" if cumple else "NO_CUMPLE_CAPACIDAD",
        "motivos": ["COMPARACION_CAPACIDAD_MAYOR_QUE_UMBRAL_AUTORIZADO"],
        "referenciaPolitica": h.referencia_evidencia}


def clave_funcional(s):
    return str(uuid5(NAMESPACE_URL, "credihoy-resultado-funcional:" + str(s.pk)))


def _firma(s):
    c = s.consentimiento_consumo
    if s.metadatos.get('modo')=='AUTOGESTION_ENVIADA':
        from .centrales_personal import verificar_autorizacion_centrales_personal
        verificar_autorizacion_centrales_personal(s,c)
        return c
    validar_documento(s, c)
    o = c.otp
    if (not c.firmado or not c.aceptado or o is None or o.solicitud_id != s.pk
            or o.estado != "VALIDADA" or o.provider != "twilio_verify" or o.verificado_at is None
            or o.canal != c.canal or not o.transaction_uuid
            or not hmac.compare_digest(c.text_hash, build_consent_hash(c.version, c.text_snapshot,
                o.transaction_uuid, o.canal, o.verificado_at))):
        raise ValueError("CONSENTIMIENTO_FIRMADO_VERIFICABLE_REQUERIDO")
    return c


def _formulario(s, p):
    if (type(p) is not dict or set(p) != {"monto", "plazoMeses", "periodicidadPago", "recogerCredihoy"}
            or type(p["monto"]) is not str or not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,2})?", p["monto"])
            or type(p["plazoMeses"]) is not int or not 1 <= p["plazoMeses"] <= 48
            or p["periodicidadPago"] != "MENSUAL" or type(p["recogerCredihoy"]) is not bool):
        raise ValueError("DATOS_SOLICITUD_INVALIDOS")
    monto = Decimal(p["monto"])
    if monto <= 0 or monto != s.monto_solicitado or p["plazoMeses"] != s.plazo_solicitado:
        raise ValueError("PARAMETROS_DISTINTOS_DEL_EXPEDIENTE_EXISTENTE")
    return {**p, "monto": format(monto, ".2f")}


def _formulario_persistido(s, raw, *, permitir_ausente=False):
    from .referencias import KEY as referencias_key
    fields = {"monto", "plazoMeses", "periodicidadPago", "recogerCredihoy"}
    # Submission metadata remains intact; only evidenced financial fields reach
    # the existing engine. Missing periodicity/collection are never defaulted.
    personal=s.metadatos.get('modo')=='AUTOGESTION_ENVIADA'
    if type(raw) is not dict or (not personal and set(raw) - fields - {referencias_key}):
        raise ValueError("DATOS_SOLICITUD_INVALIDOS")
    form = {key: value for key, value in raw.items() if key in fields}
    if not form and permitir_ausente:
        return None
    if personal:
        from .autoservicio import validar_procedencia_formulario
        evidenced=validar_procedencia_formulario(s,raw)
        form={k:evidenced[k] for k in fields}
    return _formulario(s, form)


def _digest(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _datos_obtencion(*, ingreso=False, hdc=False, politica=False):
    # No se atribuye al consumo vigente la evidencia del replay privado.
    return [
        {"fuente": "HDC", "estado": "DISPONIBLE_EN_CONTEXTO_NO_APROBACION" if hdc else "VIGENCIA_Y_APLICABILIDAD_NO_CERTIFICADAS",
         "detalle": "Historico disponible por separado; no se ejecutan nuevas consultas."},
        {"fuente": "LINIX", "estado": "DISPONIBLE_EN_CONTEXTO_NO_APROBACION" if ingreso else "INGRESO_COMPUTABLE_Y_HECHOS_GLOBALES_PENDIENTES",
         "detalle": "Salario observado no sustituye politica de ingreso ni calificacion global."},
        {"fuente": "POLITICA", "estado": "DISPONIBLE_EN_CONTEXTO_NO_APROBACION" if politica else "CONDICION_Y_EFECTOS_CAPACIDAD_PENDIENTES",
         "detalle": "Excel UNICO no establece el desenlace crediticio de capacidad cero o negativa."},
    ]


def _resultado(s, form, request_id, *, ingresos=None, cuotas=None, hechos=None, umbral_mayor_que=None):
    from .presentacion import serializar_evaluacion
    from .normativa import incorporar_smmlv, evidencia_monetaria
    fecha = timezone.now()
    entrada = EntradaCredihoyUnicoV2(monto_solicitado=Decimal(form["monto"]),
        plazo_meses=form["plazoMeses"], periodicidad_pago=form["periodicidadPago"],
        modalidad_codigo="111", recogida=RecogidaCredihoyUnicoV2(aplica=form["recogerCredihoy"]))
    capacidad = evaluar_capacidad_unico_v2(entrada,
        ingresos_mensuales=ingresos if ingresos is not None else Hecho(fuente="INGRESO_COMPUTABLE_NO_HABILITADO"),
        cuotas_deudas_hdc_brutas=cuotas if cuotas is not None else Hecho(fuente="HDC_VIGENTE_Y_APLICABLE_NO_CERTIFICADO"))
    contexto = incorporar_smmlv(hechos if hechos is not None else HechosReglasUnicoV2(),
        fecha=fecha, modalidad=entrada.modalidad_codigo)
    reglas = evaluar_reglas_unico_v2(entrada, hechos=contexto, capacidad=capacidad, fecha_evaluacion=fecha)
    result = serializar_evaluacion(capacidad, reglas, request_id,
        fuente="EXPEDIENTE_OPERATIVO_SIN_DECISION", fecha=fecha,
        evidencia_reglas=evidencia_monetaria(entrada, contexto), hechos_reglas=contexto)
    clasificacion = clasificar_capacidad(capacidad, umbral_mayor_que=umbral_mayor_que)
    result.update(versionFuncional=VERSION, solicitudId=str(s.pk), resultadoCapacidad=clasificacion,
        estadoTramite="PENDIENTE_REVISION", revisionHumanaHabilitada=False,
        motivoRevision="HECHOS_VIGENTES_Y_POLITICA_PENDIENTES_NO_RECHAZO",
        estadoObtencionDatos=_datos_obtencion(ingreso=capacidad.ingresos_mensuales.disponible,
            hdc=capacidad.cuotas_deudas_hdc_brutas.disponible, politica=clasificacion["referenciaPolitica"] is not None),
        preparacionRadicacion=preparar_expediente_unico(),
        secuencia=[{"paso": paso, "estado": estado} for paso, estado in (
            ("SOLICITUD", "RECIBIDA"), ("DATOS", "INCOMPLETOS_NO_SE_CONSULTARON_PROVEEDORES"),
            ("CAPACIDAD", clasificacion["estado"]), ("REGLAS", "EJECUTADAS_CON_FALTANTES_EXPLICITOS"),
            ("RESULTADO", clasificacion["estado"]), ("ESTADO", "PENDIENTE_REVISION"))])
    result["preparacionRadicacion"]["puntoTransicion"] = {
        "momento": "DESPUES_DECISION_AUTORIZADA_Y_CUOTA_CONTRACTUAL_ANTES_DEL_GATEWAY_LINIX",
        "servicioExistente": "api_data_v1/incluirtec/credihoy-digital/radicar/",
        "precondiciones": ["DECISIONFINAL_REAL_AUTORIZADA_NO_HISTORICA",
            "POLITICA_VERSIONADA_Y_HECHOS_VIGENTES", "TASA_Y_CUOTA_CONTRACTUALES_CERTIFICADAS",
            "CONTEXTO_LINIX_390_111_CFA_COMPLETO", "IDEMPOTENCIA_Y_AUTORIZACION_ESPECIFICA_ENVIO"],
        "envioAutomatico": False}
    return result


def estado_funcional(solicitud_id, operador):
    s = obtener(solicitud_id, operador)
    result = leer_estado_funcional(s)
    from .linix_estado import presentar
    estado = presentar(s, tecnico=True)
    if estado is not None:
        result['estadoLinix'] = estado
    from .estado_credito import construir_estado
    result['estadoCredito'] = construir_estado(s, result)
    motivo = _motivo_consulta_condiciones(s)
    result['accionesDisponibles'] = {"consultarCondiciones": {
        "habilitada": motivo is None, "motivo": motivo}}
    return result


def _motivo_consulta_condiciones(s):
    from django.conf import settings
    if str(s.pk) == "a402121e-81d0-4759-8c4c-485f0787d45c":
        return "PILOTO_EXISTENTE_SOLO_CONCILIACION"
    if s.consumo_detail.orchestration_data.get("credihoy_linix_administrativo"):
        return "EXPEDIENTE_CON_ENVIO_LINIX_SOLO_CONCILIACION"
    if getattr(settings, "CREDIHOY_TASAS_ENABLED", False) is not True:
        return "CATALOGO_COMERCIAL_NO_HABILITADO"
    return None


def consultar_condiciones(solicitud_id, operador, *, clave, request_id, autogestion=False):
    """Accion explicita de lectura API; nunca consulta HDC ni inicia LINIX."""
    from .condicion_comercial import resolver_para_solicitud
    from apps.xcore_consumo.tasas_models import SeleccionTarifaCredihoy
    with transaction.atomic():
        s=obtener_para_evaluacion(solicitud_id,operador,autogestion=autogestion,lock=True)
        _firma(s)
        if clave != request_id:
            raise ConflictoFuncional("CLAVE_CONSULTA_CONDICIONES_INCOMPATIBLE")
        motivo = _motivo_consulta_condiciones(s)
        if motivo:
            raise ConflictoFuncional(motivo)
        previous = SeleccionTarifaCredihoy.objects.filter(request_id=request_id).exists()
        seleccion = resolver_para_solicitud(s.pk, operador, request_id=request_id,autogestion=autogestion)
        if not previous:
            AuditLog.objects.create(solicitud=s, actor=f"user:{operador.pk}",
                event="CREDIHOY_CONDICIONES_CONSULTADAS", request_id=request_id,
                payload={"seleccionId": seleccion.pk, "proveedor": "API_DATA",
                    "estado": seleccion.resultado["estado"], "START": 0,
                    "autorizadoDecisionCrediticia": False})
    return leer_estado_funcional(s) if autogestion else estado_funcional(solicitud_id, operador)


def _reglas_precarga(s):
    from .precarga import recuperar_nacimiento_evidenciado
    from zoneinfo import ZoneInfo
    nacimiento = recuperar_nacimiento_evidenciado(s)
    fecha = timezone.now().astimezone(ZoneInfo("America/Bogota")).date()
    edad = Hecho(fuente="LINIX_FECHA_NACIMIENTO_NO_DISPONIBLE")
    if nacimiento.disponible and nacimiento.valor <= fecha:
        anos = fecha.year - nacimiento.valor.year - (
            (fecha.month, fecha.day) < (nacimiento.valor.month, nacimiento.valor.day))
        edad = replace(nacimiento, valor=anos, fecha_corte=fecha,
            observacion="ANOS_CUMPLIDOS_DESDE_PRECARGA_TRAZABLE; NO_CERTIFICA_IDENTIDAD_DIGITAL")
    return HechosReglasUnicoV2(edad_anios=edad)


def leer_estado_funcional(s):
    """Lectura compartida; la autorizacion del objeto corresponde al caller."""
    if oferta_custodia_activa():
        return leer_estudio_oferta_custodiado(s)
    c = _firma(s)
    detail = s.consumo_detail
    evaluation = EvaluacionConsumo.objects.filter(solicitud=s).first()
    result = evaluation.resultados.get(KEY) if evaluation and isinstance(evaluation.resultados, dict) else None
    if result is not None:
        revisiones = detail.orchestration_data.get(KEY_CONDICIONES, [])
        base_form = evaluation.input_snapshot.get('form_data') if revisiones else _formulario_persistido(s, detail.form_data)
        huella = _digest({'form': base_form, 'firma': c.text_hash, 'version': VERSION})
        if not isinstance(evaluation.input_snapshot, dict) or evaluation.input_snapshot.get('huellaFuncional') != huella or detail.orchestration_data.get(KEY, {}).get('datosGuardados') is not True:
            raise ConflictoFuncional('RESULTADO_NO_CORRESPONDE_AL_CONTEXTO_ACTUAL')
    key_hdc = KEY_HDC_REVISION if evaluation and KEY_HDC_REVISION in evaluation.resultados else KEY_HDC
    active = evaluation.input_snapshot.get('revisionHdcActiva') if evaluation else None
    if active is not None:
        from uuid import UUID
        try:
            prefix, identifier = active.rsplit(':', 1)
            if prefix != KEY_HDC_REVISION or str(UUID(identifier)) != identifier or active not in evaluation.resultados:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise ConflictoFuncional('REVISION_HDC_ACTIVA_INVALIDA') from None
        key_hdc = active
    if evaluation and key_hdc in evaluation.resultados:
        nuevo = evaluation.resultados[key_hdc]
        vinculo = evaluation.input_snapshot.get(key_hdc, {})
        from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
        op = OperacionGateway.objects.filter(pk=vinculo.get('operacionId'), solicitud=s, proveedor='HDC', estado__in=['COMPLETADA', 'SIN_INFORMACION']).first()
        if op is None or str(op.consulta_id) != vinculo.get('consultaId') or op.consentimiento_id != c.pk or (nuevo.get('solicitudId') != str(s.pk)) or (vinculo.get('firma') != c.text_hash) or (vinculo.get('resultadoSha256') != _digest(nuevo)):
            raise ConflictoFuncional('RESULTADO_HDC_NO_CORRESPONDE_AL_EXPEDIENTE')
        _exigir_recibo_hdc(op)
        revisiones = detail.orchestration_data.get(KEY_CONDICIONES, [])
        if revisiones:
            ultima = revisiones[-1]
            form = _formulario_persistido(s, detail.form_data)
            if ultima['formularioNuevo'] != form or ultima['firma'] != c.text_hash or vinculo.get('revisionCondiciones') != ultima['id'] or (vinculo.get('huellaCondiciones') != _digest(form)) or (ultima['sha256'] != _digest({k: v for k, v in ultima.items() if k != 'sha256'})):
                raise ConflictoFuncional('EVALUACION_DE_CONDICIONES_ANTERIORES')
        result = nuevo
    elif detail.orchestration_data.get(KEY_CONDICIONES):
        raise ConflictoFuncional('REVISION_CONDICIONES_SIN_EVALUACION_VIGENTE')
    state = {'versionContrato': VERSION, 'solicitudId': str(s.pk), 'idempotencyKey': clave_funcional(s), 'solicitud': {'monto': str(s.monto_solicitado), 'plazoMeses': s.plazo_solicitado, 'periodicidadPago': detail.form_data.get('periodicidadPago'), 'recogerCredihoy': detail.form_data.get('recogerCredihoy')}, 'datosGuardados': detail.orchestration_data.get(KEY, {}).get('datosGuardados') is True, 'consentimiento': {'estado': 'CONSENTIMIENTO_FIRMADO' if c.firmado else 'AUTORIZACION_CENTRALES_CONFIRMADA', 'version': c.version, 'fechaFirma': c.fecha_aceptacion.isoformat() if c.firmado else None}, 'estadoObtencionDatos': result['estadoObtencionDatos'] if result else _datos_obtencion(), 'resultado': result, 'estadoTramite': 'PENDIENTE_REVISION' if result else 'DATOS_PENDIENTES', 'decisionCrediticia': 'NO_AUTORIZADA', 'autorizadoDecisionCrediticia': False, 'radicacionLinix': 'NO_EJECUTADA'}
    return aplicar_revision_estudio_registrada(s, evaluation, state)


def registrar_resultado_consulta_hdc(solicitud_id, operador, *, operacion, ingresos, hechos_reglas, cuotas_aplicables_explicitas=None, revision_evidencia=None, autogestion=False, descuentos_nomina=None, informe_xml=None, cuota_estimada=None, titularidad_recogida=None):
    """Anexa la evaluacion real; no sustituye el resultado anterior ni decide credito."""
    from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
    from .evaluacion_controlada import evaluar_respuestas_controladas
    from uuid import UUID
    if revision_evidencia is not None and (type(revision_evidencia) is not str or str(UUID(revision_evidencia)) != revision_evidencia):
        raise ValueError('REVISION_EVIDENCIA_INVALIDA')
    key = KEY_HDC if revision_evidencia is None else KEY_HDC_REVISION
    with transaction.atomic():
        s = obtener_para_evaluacion(solicitud_id, operador, autogestion=autogestion, lock=True)
        c = _firma(s)
        op = OperacionGateway.objects.select_for_update().get(pk=operacion.operacion_id, solicitud=s, proveedor='HDC')
        if op.estado != operacion.estado or op.request_id != operacion.request_id or op.consulta_id != operacion.consulta_id or (op.consentimiento_id != c.pk) or (op.version_contrato != operacion.version_contrato) or (operacion.proveedor != 'HDC'):
            raise ConflictoFuncional('OPERACION_HDC_NO_CORRESPONDE_AL_EXPEDIENTE')
        _exigir_recibo_hdc(op)
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        form = _formulario_persistido(s, detail.form_data)
        evaluation = EvaluacionConsumo.objects.select_for_update().filter(solicitud=s).first()
        if evaluation is None and (not autogestion):
            raise EvaluacionConsumo.DoesNotExist
        if evaluation is not None and revision_evidencia is not None and (KEY_HDC_REVISION in evaluation.resultados) and (evaluation.input_snapshot[KEY_HDC_REVISION].get('revisionEvidencia') != revision_evidencia):
            key = KEY_HDC_REVISION + ':' + revision_evidencia
        leer_estado_funcional(s)
        if evaluation is not None and key in evaluation.resultados:
            if evaluation.input_snapshot[key]['operacionId'] != str(op.pk) or evaluation.input_snapshot[key].get('revisionEvidencia') != revision_evidencia:
                raise ConflictoFuncional('OTRA_EVALUACION_HDC_EXISTENTE')
            return leer_estado_funcional(s)
        if revision_evidencia is not None and (evaluation is None or KEY_HDC not in evaluation.resultados):
            raise ConflictoFuncional('REVISION_REQUIERE_EVALUACION_HDC_ORIGINAL')
        entrada = EntradaCredihoyUnicoV2(monto_solicitado=Decimal(form['monto']), plazo_meses=form['plazoMeses'], periodicidad_pago=form['periodicidadPago'], modalidad_codigo='111', recogida=RecogidaCredihoyUnicoV2(aplica=form['recogerCredihoy']))
        result = evaluar_respuestas_controladas(entrada, hdc=operacion, operaciones_esperadas={op.pk: ('HDC', op.request_id)}, ingresos=ingresos, hechos_reglas=hechos_reglas, fecha=timezone.now(), request_id=str(op.request_id), cuotas_aplicables_explicitas=cuotas_aplicables_explicitas, descuentos_nomina=descuentos_nomina, informe_xml=informe_xml, cuota_estimada=cuota_estimada, titularidad_recogida=titularidad_recogida, fecha_consulta_hdc=op.fin_at)
        result.update(versionFuncional=VERSION, solicitudId=str(s.pk), fuenteHechos='EXPEDIENTE_OPERATIVO_SIN_DECISION', resultadoCapacidad={'estado': 'NO_EVALUABLE', 'motivos': list(result['informacionInsuficiente']) or ['CONDICION_CAPACIDAD_Y_EFECTOS_NO_AUTORIZADOS'], 'referenciaPolitica': None}, estadoObtencionDatos=[{'fuente': 'HDC', 'estado': 'DISPONIBLE_EN_CONTEXTO_NO_APROBACION', 'detalle': 'Consulta real asociada al expediente; aplicabilidad financiera separada.'}, {'fuente': 'LINIX', 'estado': 'DISPONIBLE_EN_CONTEXTO_NO_APROBACION' if ingresos.disponible else 'INGRESO_COMPUTABLE_Y_HECHOS_GLOBALES_PENDIENTES', 'detalle': ingresos.observacion or 'Hecho suministrado sin nueva consulta LINIX.'}, {'fuente': 'POLITICA', 'estado': 'CONDICION_Y_EFECTOS_CAPACIDAD_PENDIENTES', 'detalle': 'Sin aprobacion ni rechazo productivo.'}], preparacionRadicacion=preparar_expediente_unico(), secuencia=[{'paso': paso, 'estado': estado} for paso, estado in (('SOLICITUD', 'RECIBIDA'), ('DATOS', 'HDC_REAL_' + op.estado), ('CAPACIDAD', result['capacidadMatematica']['estado']), ('REGLAS', 'EJECUTADAS'), ('RESULTADO', 'NO_AUTORIZADA'), ('ESTADO', 'PENDIENTE_REVISION'))])
        if evaluation is None:
            evaluation = EvaluacionConsumo(solicitud=s)
        evaluation.resultados = {**evaluation.resultados, key: result}
        evaluation.input_snapshot = {**evaluation.input_snapshot, key: {'operacionId': str(op.pk), 'consultaId': str(op.consulta_id), 'firma': c.text_hash, 'revisionEvidencia': revision_evidencia, 'resultadoSha256': _digest(result), **({'revisionCondiciones': detail.orchestration_data[KEY_CONDICIONES][-1]['id'], 'huellaCondiciones': _digest(form)} if detail.orchestration_data.get(KEY_CONDICIONES) else {})}}
        if key.startswith(KEY_HDC_REVISION + ':'):
            evaluation.input_snapshot['revisionHdcActiva'] = key
        if evaluation.pk:
            evaluation.save(update_fields=['resultados', 'input_snapshot', 'updated_at'])
        else:
            evaluation.save(force_insert=True)
        AuditLog.objects.create(solicitud=s, event='CREDIHOY_RESULTADO_HDC_REAL_REGISTRADO', actor=f'user:{operador.pk}', request_id=str(op.request_id), payload={'operacionId': str(op.pk), 'consultaId': str(op.consulta_id), 'revisionEvidencia': revision_evidencia, 'estadoTramite': 'PENDIENTE_REVISION', 'autorizadoDecisionCrediticia': False})
    return leer_estado_funcional(s) if autogestion else estado_funcional(solicitud_id, operador)


def revisar_condiciones(solicitud_id, operador, *, monto, plazo_meses, revision_id,
        referencia_autorizacion, operacion_hdc, hechos_reglas, ingresos,
        cuotas_aplicables_explicitas=None):
    """Cambio expreso con nueva evaluacion; nunca reescribe snapshots ni evidencias firmadas."""
    from uuid import UUID
    from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
    from .consulta_controlada import texto_consentimiento_controlado
    if (type(monto) is not Decimal or not monto.is_finite() or not 0 < monto <= Decimal('9999999999.99')
            or monto.as_tuple().exponent < -2 or type(plazo_meses) is not int or not 1 <= plazo_meses <= 48
            or str(UUID(str(revision_id))) != str(revision_id)
            or not isinstance(referencia_autorizacion, str) or not referencia_autorizacion.strip()
            or len(referencia_autorizacion) > 240):
        raise ValueError('REVISION_CONDICIONES_INVALIDA')
    revision_id = str(revision_id)
    with transaction.atomic():
        s = obtener(solicitud_id, operador, lock=True)
        c = _firma(s)
        if c.text_snapshot != texto_consentimiento_controlado():
            raise ConflictoFuncional('CONSENTIMIENTO_CONDICIONES_REQUIERE_ACEPTACION_ESPECIFICA')
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        evaluation = EvaluacionConsumo.objects.select_for_update().get(solicitud=s)
        revisiones = detail.orchestration_data.get(KEY_CONDICIONES, [])
        for r in revisiones:
            if r['id'] == revision_id:
                if (r['formularioNuevo']['monto'] != format(monto, '.2f')
                        or r['formularioNuevo']['plazoMeses'] != plazo_meses
                        or r['autorizacion'] != referencia_autorizacion):
                    raise ConflictoFuncional('REVISION_CONDICIONES_IDEMPOTENCIA_INCOMPATIBLE')
                return estado_funcional(s.pk, operador)
        if detail.orchestration_data.get('credihoy_linix_administrativo'):
            raise ConflictoFuncional('EXPEDIENTE_RADICADO_SOLO_CONCILIACION')
        if evaluation.decision_final != 'NO_AUTORIZADA':
            raise ConflictoFuncional('DECISION_EXISTENTE_REQUIERE_PROCEDIMIENTO_INSTITUCIONAL')
        leer_estado_funcional(s)
        previous = _formulario_persistido(s, detail.form_data)
        if Decimal(previous['monto']) == monto and previous['plazoMeses'] == plazo_meses:
            raise ConflictoFuncional('CONDICIONES_SIN_CAMBIO')
        op = OperacionGateway.objects.select_for_update().get(pk=operacion_hdc.operacion_id, solicitud=s,
            proveedor='HDC', estado='COMPLETADA', consentimiento=c)
        if (op.request_id != operacion_hdc.request_id or op.consulta_id != operacion_hdc.consulta_id
                or op.estado != operacion_hdc.estado or operacion_hdc.proveedor != 'HDC'):
            raise ConflictoFuncional('OPERACION_HDC_NO_CORRESPONDE_AL_EXPEDIENTE')
        _exigir_recibo_hdc(op)
        if any(v.get('revisionEvidencia') == revision_id for v in evaluation.input_snapshot.values() if isinstance(v, dict)):
            raise ConflictoFuncional('IDENTIFICADOR_REVISION_YA_UTILIZADO')
        nuevo = {**previous, 'monto': format(monto, '.2f'), 'plazoMeses': plazo_meses}
        registro = {'id': revision_id, 'fecha': timezone.now().isoformat(), 'autorizacion': referencia_autorizacion,
            'formularioAnterior': previous, 'formularioNuevo': nuevo, 'firma': c.text_hash,
            'consultaId': str(op.consulta_id), 'resultadoAnteriorSha256': _digest(evaluation.resultados),
            'invalida': ['CALCULO_CUOTA', 'SIMULACION', 'CONCORDANCIA_FINANCIERA', 'XML_SELLADO_POR_CONDICIONES'],
            'conserva': ['CONSENTIMIENTO', 'OTP', 'HDC', 'PAGADURIA', 'REFERENCIAS', 'TARIFA_NO_DEPENDIENTE_DEL_MONTO'],
            'anteriorSha256': revisiones[-1]['sha256'] if revisiones else None}
        registro['sha256'] = _digest(registro)
        s.monto_solicitado = monto
        s.plazo_solicitado = plazo_meses
        s.save(update_fields=['monto_solicitado', 'plazo_solicitado', 'updated_at'])
        detail.form_data = {**detail.form_data, **nuevo}
        detail.orchestration_data = {**detail.orchestration_data, KEY_CONDICIONES: [*revisiones, registro],
            KEY: {**detail.orchestration_data.get(KEY, {}), 'datosGuardados': True, 'huellaFormulario': _digest(nuevo)}}
        detail.save(update_fields=['form_data', 'orchestration_data', 'updated_at'])
        # El cambio y la nueva evaluacion son atomicos. No exponer un resultado del monto anterior.
        key = KEY_HDC_REVISION + ':' + revision_id
        from .evaluacion_controlada import evaluar_respuestas_controladas
        entrada = EntradaCredihoyUnicoV2(monto_solicitado=monto, plazo_meses=plazo_meses,
            periodicidad_pago=nuevo['periodicidadPago'], modalidad_codigo='111',
            recogida=RecogidaCredihoyUnicoV2(aplica=nuevo['recogerCredihoy']))
        result = evaluar_respuestas_controladas(entrada, hdc=operacion_hdc,
            operaciones_esperadas={op.pk: ('HDC', op.request_id)}, ingresos=ingresos,
            hechos_reglas=hechos_reglas, fecha=timezone.now(), request_id=revision_id,
            cuotas_aplicables_explicitas=cuotas_aplicables_explicitas)
        result.update(versionFuncional=VERSION, solicitudId=str(s.pk),
            resultadoCapacidad={'estado': 'NO_EVALUABLE', 'motivos': list(result['informacionInsuficiente'])
                or ['CONDICION_CAPACIDAD_Y_EFECTOS_NO_AUTORIZADOS'], 'referenciaPolitica': None},
            estadoObtencionDatos=_datos_obtencion(hdc=True), revisionCondiciones=registro,
            preparacionRadicacion=preparar_expediente_unico(),
            relecturaHdc={'consultaId': str(op.consulta_id), 'sinNuevoConsumo': True,
                'alcance': 'MISMO_EXPEDIENTE_Y_CONSENTIMIENTO_SIN_MONTO_FIRMADO',
                'vigenciaParaDecision': 'NO_CERTIFICADA'},
            secuencia=[{'paso': k, 'estado': v} for k, v in (
                ('SOLICITUD', 'RECIBIDA'), ('DATOS', 'HDC_REAL_CONSERVADA'), ('CAPACIDAD', 'NO_EVALUABLE'),
                ('REGLAS', 'EJECUTADAS'), ('RESULTADO', 'NO_AUTORIZADA'), ('ESTADO', 'PENDIENTE_REVISION'))])
        evaluation.resultados = {**evaluation.resultados, key: result}
        evaluation.input_snapshot = {**evaluation.input_snapshot, key: {'operacionId': str(op.pk),
            'consultaId': str(op.consulta_id), 'firma': c.text_hash, 'revisionEvidencia': revision_id,
            'revisionCondiciones': revision_id, 'huellaCondiciones': _digest(nuevo), 'resultadoSha256': _digest(result)},
            'revisionHdcActiva': key}
        evaluation.save(update_fields=['resultados', 'input_snapshot', 'updated_at'])
        AuditLog.objects.create(solicitud=s, actor=f'user:{operador.pk}', event='CREDIHOY_CONDICIONES_REVISADAS',
            request_id=revision_id, payload={'revision': registro, 'HDCNuevas': 0, 'OTP': 0, 'START': 0})
        return estado_funcional(s.pk, operador)


def procesar_funcional(solicitud_id, operador, *, accion, payload, clave, request_id,
                      hechos_internos=None):
    """Unico commit DB del resultado, bajo lock de Solicitud; no update de evaluaciones previas."""
    if hechos_internos is not None:
        from ..hechos import HechosInternosCrediHoyV2
        if (accion != 'evaluar' or not isinstance(hechos_internos, HechosInternosCrediHoyV2)
                or hechos_internos.metadatos is None
                or hechos_internos.metadatos.request_id != str(request_id)):
            raise ValueError('HECHOS_INTERNOS_NO_CORRESPONDEN_A_EVALUACION')
    with transaction.atomic():
        s = obtener(solicitud_id, operador, lock=True)
        c = _firma(s)
        if clave != clave_funcional(s):
            raise ConflictoFuncional("CLAVE_FUNCIONAL_INCOMPATIBLE")
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        if accion == "guardar_datos":
            form = _formulario(s, payload)
            previous = _formulario_persistido(s, detail.form_data, permitir_ausente=True)
            if previous is not None and previous != form:
                raise ConflictoFuncional("DATOS_EXISTENTES_NO_SOBRESCRIBIR")
            if previous is None:
                if EvaluacionConsumo.objects.filter(solicitud=s).exists():
                    raise ConflictoFuncional("EVALUACION_EXISTENTE_NO_SOBRESCRIBIR")
                detail.form_data = {**detail.form_data, **form}
                detail.orchestration_data = {**detail.orchestration_data,
                    KEY: {"datosGuardados": True, "huellaFormulario": _digest(form)}}
                detail.save(update_fields=["form_data", "orchestration_data", "updated_at"])
                AuditLog.objects.create(solicitud=s, event="CREDIHOY_DATOS_FUNCIONALES_GUARDADOS",
                    actor=f"user:{operador.pk}", request_id=request_id,
                    payload={"version": VERSION, "sinProveedores": True})
        elif accion == "evaluar":
            if payload != {} or detail.orchestration_data.get(KEY, {}).get("datosGuardados") is not True:
                raise ValueError("DATOS_EXPLICITOS_REQUERIDOS")
            form = _formulario_persistido(s, detail.form_data)
            huella = _digest({"form": form, "firma": c.text_hash, "version": VERSION})
            anterior = EvaluacionConsumo.objects.filter(solicitud=s).first()
            if anterior:
                if (anterior.input_snapshot.get("huellaFuncional") != huella
                        or KEY not in anterior.resultados):
                    raise ConflictoFuncional("EVALUACION_EXISTENTE_NO_SOBRESCRIBIR")
            else:
                hechos = _reglas_precarga(s)
                ingresos = None
                snapshot = {"version": VERSION, "proveedoresConsultados": [],
                            "evidenciaHistoricaNoUsadaComoVigente": True}
                if hechos_internos is not None:
                    from dataclasses import asdict
                    from .proyeccion_interna import proyectar_reglas_internas
                    from apps.xcore_consumo.services.otp_crypto import encrypt_text
                    proyectados = proyectar_reglas_internas(hechos_internos,
                        fecha=hechos_internos.metadatos.fecha_evaluacion)
                    hechos = (proyectados if proyectados.edad_anios.disponible else
                              replace(proyectados, edad_anios=hechos.edad_anios))
                    if hechos_internos.financieros is not None:
                        from .normativa import ingreso_computable_congente
                        ingresos = ingreso_computable_congente(hechos_internos.financieros)
                    snapshot.update(proveedoresConsultados=['API_DATA_HECHOS_V2'],
                        hechosInternosCifrados=encrypt_text(json.dumps(asdict(hechos_internos), default=str)),
                        requestIdHechos=str(request_id))
                result = _resultado(s, form, request_id, hechos=hechos, ingresos=ingresos)
                EvaluacionConsumo.objects.create(solicitud=s,
                    input_snapshot={"version": VERSION, "form_data": form, "huellaFuncional": huella},
                    integraciones_snapshot=snapshot,
                    resultados={KEY: result}, decision_final="NO_AUTORIZADA")
                AuditLog.objects.create(solicitud=s, event="CREDIHOY_RESULTADO_FUNCIONAL_REGISTRADO",
                    actor=f"user:{operador.pk}", request_id=request_id,
                    payload={"version": VERSION, "resultadoCapacidad": "NO_EVALUABLE",
                        "estadoTramite": "PENDIENTE_REVISION", "autorizadoDecisionCrediticia": False})
        else:
            raise ValueError("ACCION_FUNCIONAL_INVALIDA")
    return estado_funcional(solicitud_id, operador)


def aplicar_revision_estudio_registrada(s, evaluation, state):
    """Read the latest immutable revision and its already verified signed summary."""
    import base64
    from uuid import UUID
    from cryptography.exceptions import InvalidSignature
    from apps.xcore_consumo.services.otp_crypto import decrypt_text
    from apps.xcore_consumo.models import ConsentimientoConsumo
    from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
    from . import centrales_personal as central
    if evaluation is None:
        return state
    entries = list(AuditLog.objects.filter(solicitud=s, event='CREDIHOY_ESTUDIO_VERSION_REGISTRADA').order_by('created_at', 'pk')[:101])
    if not entries:
        return state
    if len(entries) > 100:
        raise ConflictoFuncional('VERSIONES_ESTUDIO_REQUIEREN_CONCILIACION')

    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, default=str).encode()

    def digest(value):
        return hashlib.sha256(canonical(value)).hexdigest()
    try:
        selected = {}
        base_hash = hashlib.sha256(json.dumps({'input': evaluation.input_snapshot, 'resultados': evaluation.resultados}, sort_keys=True, default=str).encode()).hexdigest()
        titular = hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest()
        for entry in entries:
            raw = decrypt_text(entry.payload['resultadoCifrado'])
            if hashlib.sha256(raw.encode()).hexdigest() != entry.payload['resultadoSha256']:
                raise ValueError()
            version = json.loads(raw)
            reference = str(UUID(version['versionReferencia']))
            request = version['fuentes']['huellaSolicitud']
            if str(entry.request_id) != reference or reference != version['versionReferencia'] or version['solicitudId'] != str(s.pk) or (version['evaluacionBaseId'] != evaluation.pk) or (type(version['version']) is not int) or (version['version'] < 2) or (request['documentoSha256'] != titular) or (request['evaluacionSha256'] != base_hash) or (version['resultado']['requestId'] != reference):
                raise ValueError()
            if version['version'] in selected:
                raise ValueError()
            selected[version['version']] = (entry, version)
        entry, version = selected[max(selected)]
        reference = version['versionReferencia']
        op = OperacionGateway.objects.get(pk=entry.payload['recibo']['operacionHdc'], solicitud=s, proveedor='HDC', estado__in=['COMPLETADA', 'SIN_INFORMACION'])
        _exigir_recibo_hdc(op)
        key = str(uuid5(NAMESPACE_URL, 'credihoy-estudio-publico:' + str(s.pk) + ':' + reference))
        summary = AuditLog.objects.get(solicitud=s, event='CREDIHOY_ESTUDIO_PUBLICO_VERIFICADO', request_id=key)
        envelope = summary.payload
        body = envelope['payload']
        study = body['estudio']
        if envelope['algoritmo'] != 'Ed25519':
            raise ValueError()
        central._clave_publica().verify(base64.b64decode(envelope['firmaBase64'], validate=True), b'credihoy-estudio-publico-tecnico-v1\x00' + canonical(body))
        consent = ConsentimientoConsumo.objects.values().get(solicitud=s)
        detail = SolicitudConsumo.objects.values().get(solicitud=s)
        current = EvaluacionConsumo.objects.values().get(pk=evaluation.pk, solicitud=s)
        if body['version'] not in {'credihoy-estudio-tecnico-firmado-v1', 'credihoy-estudio-tecnico-firmado-v2'} or body['proposito'] != 'LECTURA_ESTUDIO_NO_DECISION' or body['solicitudId'] != str(s.pk) or (body['titularSha256'] != titular) or (body['evaluacionBaseSha256'] != digest(current)) or (body['consentimientoSha256'] != digest(consent)) or (body['consumoSha256'] != huella_consumo_estudio(detail, body['version'])) or (study['version'] != version['version']) or (study['versionContrato'] != 'credihoy-estudio-publico-v1') or (study['referenciaVersion'] != reference) or (study['evaluacionBaseId'] != evaluation.pk) or (study['solicitudId'] != str(s.pk)) or (study['autorizadoDecisionCrediticia'] is not False):
            raise ValueError()
        rules = version['resultado']['reglas']
        if len(rules) != 16 or len({r['codigo'] for r in rules}) != 16:
            raise ValueError()
        projected = [{k: r[k] for k in ('codigo', 'nombre', 'resultado', 'estadoEvaluacion', 'motivos', 'datosFaltantes')} for r in rules]
        if study['reglas'] != projected:
            raise ValueError()
        return {**state, 'resultado': version['resultado'], 'revisionEstudio': {'version': version['version'], 'versionReferencia': reference, 'versionSha256': entry.payload['resultadoSha256'], 'resultadoSha256': _digest(version['resultado']), 'estudioSha256': digest(study), 'evaluacionBaseId': evaluation.pk, 'operacionHdc': str(op.pk), 'revisionHdcActiva': evaluation.input_snapshot.get('revisionHdcActiva'), 'condicionesYConsentimientoVerificados': True, 'firmaResumenVerificada': True}}
    except (KeyError, TypeError, ValueError, AttributeError, InvalidSignature, AuditLog.DoesNotExist, AuditLog.MultipleObjectsReturned, OperacionGateway.DoesNotExist):
        raise ConflictoFuncional('REVISION_ESTUDIO_VIGENTE_NO_VERIFICABLE') from None


def recuperar_respuesta_hdc_custodiada(solicitud_id):
    """Recover a bound normalized report from custody, never from a new query."""
    import base64
    from apps.integraciones.risk_gateway.clientes import HDCGatewayClient
    from apps.integraciones.risk_gateway.contratos import HDCNormalizado
    from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
    from apps.integraciones.risk_gateway.operaciones.servicio import ResultadoOperacion
    from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text
    from .embargos import informe_hdc_concordante
    if transaction.get_connection().in_atomic_block:
        raise ValueError('CUSTODIA_REQUIERE_AUTOCOMMIT')
    op = OperacionGateway.objects.select_related('solicitud__solicitante').get(solicitud_id=solicitud_id, proveedor='HDC', estado='COMPLETADA')
    _exigir_recibo_hdc(op)
    query = AuditLog.objects.filter(solicitud_id=solicitud_id, event='CREDIHOY_HDC_RESPUESTA_RECUPERADA', request_id=str(op.request_id))
    saved = query.first()
    if saved is not None:
        raw = decrypt_text(saved.payload['contenidoCifrado'])
        if hashlib.sha256(raw.encode()).hexdigest() != saved.payload['sha256']:
            raise ValueError('CUSTODIA_HDC_NO_INTEGRA')
        packet = json.loads(raw)
        normalized = HDCNormalizado.desde_payload(packet['normalizado'])
        xml = base64.b64decode(packet['xmlBase64'], validate=True)
        if hashlib.sha256(xml).hexdigest() != packet['xmlSha256']:
            raise ValueError('CUSTODIA_XML_NO_INTEGRA')
    else:
        normalized, xml = HDCGatewayClient().recuperar_respuesta_custodiada(str(op.consulta_id), request_id=str(op.request_id), titular_sha256=hashlib.sha256(op.solicitud.solicitante.numero_identificacion.encode()).hexdigest())
    if normalized['requestId'] != str(op.request_id) or normalized['resultado']['consulta_id'] != str(op.consulta_id) or normalized['versionContrato'] != op.version_contrato:
        raise ValueError('CUSTODIA_HDC_DE_OTRA_OPERACION')
    informe_hdc_concordante(xml, normalized)
    if saved is None:
        packet = {'normalizado': normalized.a_dict(), 'xmlBase64': base64.b64encode(xml).decode(), 'xmlSha256': hashlib.sha256(xml).hexdigest(), 'normalizadoReconstruidoDesdeXML': True, 'consultaNueva': False}
        raw = json.dumps(packet, sort_keys=True, default=str)
        with transaction.atomic(durable=True):
            locked = OperacionGateway.objects.select_for_update().get(pk=op.pk)
            _exigir_recibo_hdc(locked)
            if locked.estado != 'COMPLETADA' or locked.consulta_id != op.consulta_id:
                raise ValueError('CUSTODIA_HDC_OPERACION_CAMBIO')
            previous = query.first()
            if previous is not None and previous.payload['sha256'] != hashlib.sha256(raw.encode()).hexdigest():
                raise ValueError('CUSTODIA_HDC_CONFLICTIVA')
            if previous is None:
                AuditLog.objects.create(solicitud_id=solicitud_id, event='CREDIHOY_HDC_RESPUESTA_RECUPERADA', request_id=str(op.request_id), actor='servicio:continuacion', payload={'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'contenidoCifrado': encrypt_text(raw), 'consultaNueva': False, 'operacionId': str(op.pk)})
    result = ResultadoOperacion(op.pk, 'HDC', op.estado, op.request_id, op.consulta_id, op.version_contrato, '', True, normalized)
    proof = hashlib.sha256(json.dumps(normalized.a_dict(), sort_keys=True, default=str).encode()).hexdigest()
    return (result, xml, proof, op.fin_at)


def preparar_revision_estudio(solicitud_id, operador):
    """Append an immutable draft from fresh facts; never update the base study."""
    from dataclasses import asdict
    from uuid import uuid4
    from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text
    from .hechos_continuacion import obtener as obtener_hechos
    from .evaluacion_controlada import evaluar_respuestas_controladas
    if transaction.get_connection().in_atomic_block:
        raise ValueError('REVISION_REQUIERE_AUTOCOMMIT')
    if not operador.is_authenticated or not operador.is_active or (not operador.is_staff) or (not operador.has_perm('xcore_consumo.add_evaluacionconsumo')):
        raise ValueError('REVISION_OPERADOR_NO_AUTORIZADO')
    case = obtener_para_evaluacion(solicitud_id, operador, autogestion=True, revision_conservada=True)
    consent = _firma(case)
    evaluation = EvaluacionConsumo.objects.get(solicitud=case)
    leer_estado_funcional(case)
    if evaluation.input_snapshot.get('revisionHdcActiva'):
        raise ValueError('REVISION_HISTORICA_NO_MODIFICABLE_POR_CONTINUIDAD')
    base_hash = hashlib.sha256(json.dumps({'input': evaluation.input_snapshot, 'resultados': evaluation.resultados}, sort_keys=True, default=str).encode()).hexdigest()
    detail = SolicitudConsumo.objects.get(solicitud=case)
    case.consumo_detail = detail
    form = _formulario_persistido(case, detail.form_data)
    form_hash = _digest(form)
    consent_hash = consent.text_hash
    context = obtener_hechos(case, str(uuid4()), con_contexto=True)
    from .normativa import incorporar_smmlv
    context = replace(context, reglas=incorporar_smmlv(context.reglas, fecha=context.fecha_evaluacion or timezone.now(), modalidad='111'))
    operation, xml, proof, queried = recuperar_respuesta_hdc_custodiada(case.pk)

    def semantic(fact):
        return {k: v for k, v in fact.a_dict().items() if k in {'valor', 'disponible', 'fuente', 'ruta_origen', 'estado_certificacion'}}
    from .evaluacion_controlada import VERSION_CAPACIDAD_DECISORIA
    sources = {'versionCadena': 'credihoy-cadena-20261105-v1', 'versionCapacidadDecisoria': VERSION_CAPACIDAD_DECISORIA, 'formularioSha256': form_hash, 'reglas': {name: semantic(getattr(context.reglas, name)) for name in asdict(context.reglas)}, 'ingresos': semantic(context.ingresos), 'nomina': semantic(context.descuentos_nomina), 'cuota': {**semantic(context.cuota_estimada), 'condicion': context.cuota_estimada.observacion}, 'respuestaHdcSha256': proof, 'xmlSha256': hashlib.sha256(xml).hexdigest()}
    drafts = AuditLog.objects.filter(solicitud=case, event='CREDIHOY_ESTUDIO_REVISION_PREPARADA')
    fingerprint = _digest(sources)
    reference = str(uuid5(NAMESPACE_URL, 'credihoy-revision-estudio:' + str(case.pk) + ':' + fingerprint))
    existing = drafts.filter(request_id=reference).first()
    legacy_existing = None
    if existing is not None:
        raw = decrypt_text(existing.payload['resultadoCifrado'])
        if hashlib.sha256(raw.encode()).hexdigest() != existing.payload['resultadoSha256']:
            raise ValueError('REVISION_CONSERVADA_NO_INTEGRA')
        if 'decisionDigital' not in json.loads(raw)['resultado']:
            legacy_existing = existing
    from .elegibilidad import contexto_desde_estudio, evaluar_decision_digital
    from .condiciones_producto import politica_digital
    decision_time = timezone.now()
    observed = contexto_desde_estudio(case, evaluation.resultados[KEY_HDC], ahora=decision_time)
    from .normativa import actualizacion_al_corte
    from .estamentos import BLOQUEADO, actualizar_estamentos
    updated = actualizacion_al_corte(context.reglas.fecha_actualizacion_datos, context.fecha_evaluacion or timezone.now())
    if updated and updated['cumple'] and (observed.clasificacion_estamentos['estado'] == BLOQUEADO):
        try:
            actualizar_estamentos(case.pk, actor=operador, autogestion=True, revision_conservada=True)
        except ValueError:
            pass
        decision_time = timezone.now()
        observed = contexto_desde_estudio(case, evaluation.resultados[KEY_HDC], ahora=decision_time)
    if legacy_existing is not None and observed.clasificacion_estamentos['estado'] == BLOQUEADO:
        return legacy_existing.pk
    sources['decisionFuentes'] = {'clasificacionEstamentos': {k: v for k, v in observed.clasificacion_estamentos.items() if k not in ('consultaReferencia', 'fechaConsulta')}, 'politicaSha256': _digest(politica_digital())}
    fingerprint = _digest(sources)
    reference = str(uuid5(NAMESPACE_URL, 'credihoy-revision-estudio:' + str(case.pk) + ':' + fingerprint))
    existing = drafts.filter(request_id=reference).first()
    if existing is not None:
        return existing.pk
    entry = EntradaCredihoyUnicoV2(monto_solicitado=Decimal(form['monto']), plazo_meses=form['plazoMeses'], periodicidad_pago=form['periodicidadPago'], modalidad_codigo='111', recogida=RecogidaCredihoyUnicoV2(aplica=form['recogerCredihoy']))
    evaluated = evaluar_respuestas_controladas(entry, hdc=operation, operaciones_esperadas={operation.operacion_id: ('HDC', operation.request_id)}, ingresos=context.ingresos, hechos_reglas=context.reglas, fecha=context.fecha_evaluacion or timezone.now(), request_id=reference, descuentos_nomina=context.descuentos_nomina, cuota_estimada=context.cuota_estimada if context.cuota_estimada.disponible else None, informe_xml=xml, fecha_consulta_hdc=queried, custodia_respuesta_sha256=proof)
    evaluated.update(solicitudId=str(case.pk), estadoTramite='SUBSANACION_REQUERIDA' if any((r['codigo'] == 'UNICO_FILA_9' and r['estadoEvaluacion'] == 'NO_CUMPLE' for r in evaluated['reglas'])) else 'EN_REVISION', HDCNuevas=0)
    decision_context = contexto_desde_estudio(case, evaluated, ahora=decision_time)
    evaluated['decisionDigital'] = evaluar_decision_digital(decision_context, ahora=decision_time)
    with transaction.atomic(durable=True):
        locked = obtener_para_evaluacion(case.pk, operador, autogestion=True, lock=True, revision_conservada=True)
        current = EvaluacionConsumo.objects.get(pk=evaluation.pk, solicitud=locked)
        current_hash = hashlib.sha256(json.dumps({'input': current.input_snapshot, 'resultados': current.resultados}, sort_keys=True, default=str).encode()).hexdigest()
        current_detail = SolicitudConsumo.objects.get(solicitud=locked)
        if current_hash != base_hash or _firma(locked).text_hash != consent_hash or _digest(_formulario_persistido(locked, current_detail.form_data)) != form_hash:
            raise ValueError('BASE_ESTUDIO_CAMBIO_DURANTE_REVISION')
        existing = drafts.filter(request_id=reference).first()
        if existing is not None:
            return existing.pk
        previous = list(drafts.values_list('payload', flat=True)[:101])
        if len(previous) > 100:
            raise ValueError('REVISIONES_REQUIEREN_CONCILIACION')
        number = max([v['version'] for v in previous] + [1]) + 1
        version = {'solicitudId': str(case.pk), 'evaluacionBaseId': evaluation.pk, 'version': number, 'versionReferencia': reference, 'resultado': evaluated, 'fuentes': {'huellaSolicitud': {'documentoSha256': hashlib.sha256(case.solicitante.numero_identificacion.encode()).hexdigest(), 'evaluacionSha256': base_hash}, 'semantica': sources}}
        raw = json.dumps(version, sort_keys=True, default=str)
        record = AuditLog.objects.create(solicitud=locked, event='CREDIHOY_ESTUDIO_REVISION_PREPARADA', request_id=reference, actor='user:' + str(operador.pk), payload={'version': number, 'resultadoCifrado': encrypt_text(raw), 'resultadoSha256': hashlib.sha256(raw.encode()).hexdigest(), 'recibo': {'operacionHdc': str(operation.operacion_id)}, 'HDCNuevas': 0})
        return record.pk


def recuperar_xml_hdc(solicitud_id, *, operacion):
    """Read and encrypt owned evidence outside any financial DB transaction."""
    import base64
    from apps.integraciones.risk_gateway.clientes import HDCGatewayClient
    from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
    from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text
    from .embargos import informe_hdc_concordante
    if transaction.get_connection().in_atomic_block:
        raise ValueError('XML_HDC_REQUIERE_AUTOCOMMIT')
    op = OperacionGateway.objects.select_related('solicitud__solicitante').get(pk=operacion.operacion_id, solicitud_id=solicitud_id, proveedor='HDC', estado='COMPLETADA')
    if op.request_id != operacion.request_id or op.consulta_id != operacion.consulta_id:
        raise ValueError('XML_HDC_OPERACION_INCOMPATIBLE')
    _exigir_recibo_hdc(op)
    source = AuditLog.objects.filter(solicitud_id=solicitud_id, event='CREDIHOY_HDC_XML_CUSTODIADO', request_id=str(op.request_id))

    def read(entry):
        raw = base64.b64decode(decrypt_text(entry.payload['xmlCifrado']), validate=True)
        if entry.payload['operacionId'] != str(op.pk) or hashlib.sha256(raw).hexdigest() != entry.payload['sha256']:
            raise ValueError('XML_HDC_CUSTODIA_INVALIDA')
        informe_hdc_concordante(raw, operacion.respuesta)
        return raw
    existing = source.first()
    if existing is not None:
        return read(existing)
    raw = HDCGatewayClient().obtener_xml(str(op.consulta_id), request_id=str(op.request_id), titular_sha256=hashlib.sha256(op.solicitud.solicitante.numero_identificacion.encode()).hexdigest())
    informe_hdc_concordante(raw, operacion.respuesta)
    with transaction.atomic(durable=True):
        locked = OperacionGateway.objects.select_for_update().get(pk=op.pk)
        if locked.estado != 'COMPLETADA' or locked.request_id != op.request_id:
            raise ValueError('XML_HDC_OPERACION_CAMBIO')
        existing = source.first()
        if existing is not None:
            return read(existing)
        AuditLog.objects.create(solicitud_id=solicitud_id, event='CREDIHOY_HDC_XML_CUSTODIADO', request_id=str(op.request_id), actor='servicio:continuacion', payload={'operacionId': str(op.pk), 'consultaId': str(op.consulta_id), 'sha256': hashlib.sha256(raw).hexdigest(), 'xmlCifrado': encrypt_text(base64.b64encode(raw).decode()), 'consultaNueva': False})
    return raw


def huella_consumo_estudio(detail, version='credihoy-estudio-tecnico-firmado-v1'):
    """Separate immutable study inputs from append-only downstream operations."""
    from copy import deepcopy
    if version == 'credihoy-estudio-tecnico-firmado-v1':
        value = detail
    elif version == 'credihoy-estudio-tecnico-firmado-v2':
        value = {k: deepcopy(detail[k]) for k in ('id', 'solicitud_id', 'created_at', 'form_data', 'core_data', 'selected_hc2_keys')}
        downstream = {'credihoy_cotizacion_institucional', 'credihoy_cotizacion_institucional_escenarios_v1', 'credihoy_calendario_pago_v1', 'credihoy_calendario_pago_v1_revalidacion', 'credihoy_calendario_pago_v1_revalidacion_historial', 'credihoy_composiciones_contractuales_v1', 'credihoy_ofertas_linix_v1', 'credihoy_oferta_linix_actual', 'credihoy_aceptaciones_oferta_v1', 'credihoy_autentic_envios_v1', 'credihoy_autentic_identidad_v1', 'credihoy_formalizacion_autentic_v1', 'credihoy_otp_contractual_v1'}
        value['orchestration_data'] = {k: deepcopy(v) for k, v in detail['orchestration_data'].items() if k not in downstream}
    else:
        raise ValueError('HUELLA_ESTUDIO_VERSION_NO_ADMITIDA')
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, default=str).encode()).hexdigest()


def oferta_custodia_activa():
    from django.conf import settings
    from .publico_restringido import alias_cotizacion
    return alias_cotizacion() != 'default' and getattr(settings, 'CREDIHOY_OFERTA_CUSTODIA_ENABLED', False) is True


def leer_estudio_oferta_custodiado(s):
    """Read native registered evidence through its projection, not the full journal."""
    from django.db import connections
    from .publico_restringido import alias_cotizacion
    from .autoservicio import _estudio_publico_personal
    from .publico_institucional import project_study_decision
    from apps.xcore_consumo.services.otp_crypto import decrypt_text, OTPCryptoError
    if not oferta_custodia_activa():
        raise ConflictoFuncional('CUSTODIA_OFERTA_NO_HABILITADA')
    _firma(s)
    with connections[alias_cotizacion()].cursor() as cursor:
        cursor.execute('SELECT request_id,revision_cifrada,decision,operacion_hdc FROM public.credihoy_oferta_estudio_custodia WHERE solicitud_id=%s', [str(s.pk)])
        rows = cursor.fetchmany(2)
    if len(rows) != 1:
        raise ConflictoFuncional('CUSTODIA_OFERTA_ESTUDIO_NO_UNICO')
    reference, payload, decision, hdc = rows[0]
    try:
        if type(payload) is str:
            payload = json.loads(payload)
        if type(decision) is str:
            decision = json.loads(decision)
        encrypted = payload['resultadoCifrado']
        if type(encrypted) is not str or not 40 <= len(encrypted) <= 4 * 1024 * 1024:
            raise ValueError()
        raw = decrypt_text(encrypted)
        if hashlib.sha256(raw.encode()).hexdigest() != payload['resultadoSha256']:
            raise ValueError()
        revision = json.loads(raw)
        snapshot = revision['resultado']
        reference = str(reference)
        public = _estudio_publico_personal(s, {'numeroDocumento': s.solicitante.numero_identificacion})
        if public is None:
            raise ValueError()
        person = hashlib.sha256(s.solicitante.numero_identificacion.encode()).hexdigest()

        def digest(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
        if revision['solicitudId'] != str(s.pk) or snapshot['solicitudId'] != str(s.pk) or revision['versionReferencia'] != reference or (snapshot['requestId'] != reference) or (revision['fuentes']['huellaSolicitud']['documentoSha256'] != person) or (public['referenciaVersion'] != reference) or (public['version'] != revision['version']) or (public['evaluacionBaseId'] != revision['evaluacionBaseId']) or (decision['solicitudId'] != str(s.pk)) or (decision['revisionReferencia'] != reference) or (decision['evaluacionSha256'] != digest(snapshot)) or (decision['sha256'] != digest({k: v for k, v in decision.items() if k != 'sha256'})) or (decision['resultado']['reglasOriginales'] != snapshot['reglas']) or (public.get('decisionPolitica') != project_study_decision(decision['resultado'])) or (str(payload['recibo']['operacionHdc']) != str(hdc)):
            raise ValueError()
        from datetime import datetime
        date = datetime.fromisoformat(decision['registradoEn'])
        if date.utcoffset() is None or date > timezone.now():
            raise ValueError()
        projected = [{k: r[k] for k in ('codigo', 'nombre', 'resultado', 'estadoEvaluacion', 'motivos', 'datosFaltantes')} for r in snapshot['reglas']]
        if public['reglas'] != projected:
            raise ValueError()
        evaluation = EvaluacionConsumo.objects.get(pk=revision['evaluacionBaseId'], solicitud=s)
        base_hash = digest({'input': evaluation.input_snapshot, 'resultados': evaluation.resultados})
        if revision['fuentes']['huellaSolicitud']['evaluacionSha256'] != base_hash:
            raise ValueError()
        from uuid import UUID
        if str(UUID(decision['operacionId'])) != decision['operacionId']:
            raise ValueError()
        return {'resultado': snapshot, 'decisionCustodiada': decision, 'revisionEstudio': {'version': revision['version'], 'versionReferencia': reference, 'versionSha256': payload['resultadoSha256'], 'resultadoSha256': _digest(snapshot), 'estudioSha256': hashlib.sha256(json.dumps(public, sort_keys=True, separators=(',', ':'), ensure_ascii=True, default=str).encode()).hexdigest(), 'evaluacionBaseId': evaluation.pk, 'operacionHdc': str(hdc), 'revisionHdcActiva': evaluation.input_snapshot.get('revisionHdcActiva'), 'condicionesYConsentimientoVerificados': True, 'firmaResumenVerificada': True}}
    except (KeyError, ValueError, TypeError, AttributeError, OTPCryptoError, EvaluacionConsumo.DoesNotExist):
        raise ConflictoFuncional('CUSTODIA_OFERTA_EVIDENCIA_NO_VERIFICABLE') from None
