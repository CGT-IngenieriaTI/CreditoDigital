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


def obtener_para_evaluacion(solicitud_id, operador, *, autogestion=False, lock=False):
    if not autogestion:return obtener(solicitud_id,operador,lock=lock)
    from django.conf import settings
    from apps.solicitudes.models import Solicitud
    from .autoservicio import validar_vinculo_consumo
    if (getattr(settings,'CREDIHOY_EVALUACION_AUTOGESTION_ENABLED',False) is not True
            or not operador.is_authenticated or not operador.is_active or not operador.is_staff
            or not operador.has_perm('xcore_consumo.add_evaluacionconsumo')):
        raise ConflictoFuncional('EVALUACION_AUTOGESTION_NO_HABILITADA')
    query=Solicitud.objects.select_related('solicitante')
    if lock:query=query.select_for_update(of=('self',))
    return validar_vinculo_consumo(query.get(pk=solicitud_id,asesor__isnull=True,
        metadatos__modo='AUTOGESTION_ENVIADA'))


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
    c = _firma(s)
    detail = s.consumo_detail
    evaluation = EvaluacionConsumo.objects.filter(solicitud=s).first()
    result = evaluation.resultados.get(KEY) if evaluation and isinstance(evaluation.resultados, dict) else None
    if result is not None:
        revisiones = detail.orchestration_data.get(KEY_CONDICIONES, [])
        base_form = (evaluation.input_snapshot.get('form_data') if revisiones else
            _formulario_persistido(s, detail.form_data))
        huella = _digest({"form": base_form, "firma": c.text_hash, "version": VERSION})
        if (not isinstance(evaluation.input_snapshot, dict)
                or evaluation.input_snapshot.get("huellaFuncional") != huella
                or detail.orchestration_data.get(KEY, {}).get("datosGuardados") is not True):
            raise ConflictoFuncional("RESULTADO_NO_CORRESPONDE_AL_CONTEXTO_ACTUAL")
    key_hdc = KEY_HDC_REVISION if evaluation and KEY_HDC_REVISION in evaluation.resultados else KEY_HDC
    active = evaluation.input_snapshot.get("revisionHdcActiva") if evaluation else None
    if active is not None:
        from uuid import UUID
        try:
            prefix, identifier = active.rsplit(":", 1)
            if prefix != KEY_HDC_REVISION or str(UUID(identifier)) != identifier or active not in evaluation.resultados:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            raise ConflictoFuncional("REVISION_HDC_ACTIVA_INVALIDA") from None
        key_hdc = active
    if evaluation and key_hdc in evaluation.resultados:
        nuevo = evaluation.resultados[key_hdc]
        vinculo = evaluation.input_snapshot.get(key_hdc, {})
        from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
        op = OperacionGateway.objects.filter(pk=vinculo.get("operacionId"), solicitud=s,
            proveedor="HDC", estado__in=["COMPLETADA", "SIN_INFORMACION"]).first()
        if (op is None or str(op.consulta_id) != vinculo.get("consultaId")
                or op.consentimiento_id != c.pk
                or nuevo.get("solicitudId") != str(s.pk)
                or vinculo.get("firma") != c.text_hash
                or vinculo.get("resultadoSha256") != _digest(nuevo)):
            raise ConflictoFuncional("RESULTADO_HDC_NO_CORRESPONDE_AL_EXPEDIENTE")
        _exigir_recibo_hdc(op)
        revisiones = detail.orchestration_data.get(KEY_CONDICIONES, [])
        if revisiones:
            ultima = revisiones[-1]
            form = _formulario_persistido(s, detail.form_data)
            if (ultima['formularioNuevo'] != form or ultima['firma'] != c.text_hash
                    or vinculo.get('revisionCondiciones') != ultima['id']
                    or vinculo.get('huellaCondiciones') != _digest(form)
                    or ultima['sha256'] != _digest({k: v for k, v in ultima.items() if k != 'sha256'})):
                raise ConflictoFuncional('EVALUACION_DE_CONDICIONES_ANTERIORES')
        result = nuevo
    elif detail.orchestration_data.get(KEY_CONDICIONES):
        raise ConflictoFuncional('REVISION_CONDICIONES_SIN_EVALUACION_VIGENTE')
    return {"versionContrato": VERSION, "solicitudId": str(s.pk), "idempotencyKey": clave_funcional(s),
        "solicitud": {"monto": str(s.monto_solicitado), "plazoMeses": s.plazo_solicitado,
            "periodicidadPago": detail.form_data.get("periodicidadPago"),
            "recogerCredihoy": detail.form_data.get("recogerCredihoy")},
        "datosGuardados": detail.orchestration_data.get(KEY, {}).get("datosGuardados") is True,
        "consentimiento": {"estado": "CONSENTIMIENTO_FIRMADO" if c.firmado else "AUTORIZACION_CENTRALES_CONFIRMADA",
            "version": c.version,"fechaFirma": c.fecha_aceptacion.isoformat() if c.firmado else None},
        "estadoObtencionDatos": result["estadoObtencionDatos"] if result else _datos_obtencion(), "resultado": result,
        "estadoTramite": "PENDIENTE_REVISION" if result else "DATOS_PENDIENTES",
        "decisionCrediticia": "NO_AUTORIZADA", "autorizadoDecisionCrediticia": False,
        "radicacionLinix": "NO_EJECUTADA"}


def registrar_resultado_consulta_hdc(solicitud_id, operador, *, operacion,
                                   ingresos, hechos_reglas, cuotas_aplicables_explicitas=None,
                                   revision_evidencia=None, autogestion=False):
    """Anexa la evaluacion real; no sustituye el resultado anterior ni decide credito."""
    from apps.integraciones.risk_gateway.operaciones.models import OperacionGateway
    from .evaluacion_controlada import evaluar_respuestas_controladas
    from uuid import UUID
    if revision_evidencia is not None and (
            type(revision_evidencia) is not str or str(UUID(revision_evidencia)) != revision_evidencia):
        raise ValueError("REVISION_EVIDENCIA_INVALIDA")
    key = KEY_HDC if revision_evidencia is None else KEY_HDC_REVISION
    with transaction.atomic():
        s=obtener_para_evaluacion(solicitud_id,operador,autogestion=autogestion,lock=True)
        c = _firma(s)
        op = OperacionGateway.objects.select_for_update().get(pk=operacion.operacion_id, solicitud=s, proveedor="HDC")
        if (op.estado != operacion.estado or op.request_id != operacion.request_id
                or op.consulta_id != operacion.consulta_id or op.consentimiento_id != c.pk
                or op.version_contrato != operacion.version_contrato or operacion.proveedor != 'HDC'):
            raise ConflictoFuncional("OPERACION_HDC_NO_CORRESPONDE_AL_EXPEDIENTE")
        _exigir_recibo_hdc(op)
        detail = SolicitudConsumo.objects.select_for_update().get(solicitud=s)
        form = _formulario_persistido(s, detail.form_data)
        evaluation = EvaluacionConsumo.objects.select_for_update().filter(solicitud=s).first()
        if evaluation is None and not autogestion:raise EvaluacionConsumo.DoesNotExist
        if (evaluation is not None and revision_evidencia is not None and KEY_HDC_REVISION in evaluation.resultados
                and evaluation.input_snapshot[KEY_HDC_REVISION].get("revisionEvidencia") != revision_evidencia):
            key = KEY_HDC_REVISION + ":" + revision_evidencia
        # Comprueba tambien el resultado previo y el formulario antes de anexar.
        leer_estado_funcional(s)
        if evaluation is not None and key in evaluation.resultados:
            if (evaluation.input_snapshot[key]["operacionId"] != str(op.pk)
                    or evaluation.input_snapshot[key].get("revisionEvidencia") != revision_evidencia):
                raise ConflictoFuncional("OTRA_EVALUACION_HDC_EXISTENTE")
            return leer_estado_funcional(s)
        if revision_evidencia is not None and (evaluation is None or KEY_HDC not in evaluation.resultados):
            raise ConflictoFuncional("REVISION_REQUIERE_EVALUACION_HDC_ORIGINAL")
        entrada = EntradaCredihoyUnicoV2(monto_solicitado=Decimal(form["monto"]),
            plazo_meses=form["plazoMeses"], periodicidad_pago=form["periodicidadPago"],
            modalidad_codigo="111", recogida=RecogidaCredihoyUnicoV2(aplica=form["recogerCredihoy"]))
        result = evaluar_respuestas_controladas(entrada, hdc=operacion,
            operaciones_esperadas={op.pk: ("HDC", op.request_id)}, ingresos=ingresos,
            hechos_reglas=hechos_reglas, fecha=timezone.now(), request_id=str(op.request_id),
            cuotas_aplicables_explicitas=cuotas_aplicables_explicitas)
        result.update(versionFuncional=VERSION, solicitudId=str(s.pk),
            fuenteHechos="EXPEDIENTE_OPERATIVO_SIN_DECISION",
            resultadoCapacidad={"estado": "NO_EVALUABLE", "motivos":
                list(result["informacionInsuficiente"]) or
                ["CONDICION_CAPACIDAD_Y_EFECTOS_NO_AUTORIZADOS"], "referenciaPolitica": None},
            estadoObtencionDatos=[
                {"fuente": "HDC", "estado": "DISPONIBLE_EN_CONTEXTO_NO_APROBACION",
                 "detalle": "Consulta real asociada al expediente; aplicabilidad financiera separada."},
                {"fuente": "LINIX", "estado": "DISPONIBLE_EN_CONTEXTO_NO_APROBACION" if ingresos.disponible else "INGRESO_COMPUTABLE_Y_HECHOS_GLOBALES_PENDIENTES",
                 "detalle": ingresos.observacion or "Hecho suministrado sin nueva consulta LINIX."},
                {"fuente": "POLITICA", "estado": "CONDICION_Y_EFECTOS_CAPACIDAD_PENDIENTES",
                 "detalle": "Sin aprobacion ni rechazo productivo."}],
            preparacionRadicacion=preparar_expediente_unico(),
            secuencia=[{"paso": paso, "estado": estado} for paso, estado in (
                ("SOLICITUD", "RECIBIDA"), ("DATOS", "HDC_REAL_" + op.estado),
                ("CAPACIDAD", result["capacidadMatematica"]["estado"]), ("REGLAS", "EJECUTADAS"),
                ("RESULTADO", "NO_AUTORIZADA"), ("ESTADO", "PENDIENTE_REVISION"))])
        # No row exists until a bound, typed and completed HDC response has
        # passed the genuine signature gate and the real sixteen-rule engine.
        if evaluation is None:evaluation=EvaluacionConsumo(solicitud=s)
        evaluation.resultados = {**evaluation.resultados, key: result}
        evaluation.input_snapshot = {**evaluation.input_snapshot, key: {
            "operacionId": str(op.pk), "consultaId": str(op.consulta_id), "firma": c.text_hash,
            "revisionEvidencia": revision_evidencia,
            "resultadoSha256": _digest(result),
            **({'revisionCondiciones': detail.orchestration_data[KEY_CONDICIONES][-1]['id'],
                'huellaCondiciones': _digest(form)} if detail.orchestration_data.get(KEY_CONDICIONES) else {})}}
        if key.startswith(KEY_HDC_REVISION + ":"):
            evaluation.input_snapshot["revisionHdcActiva"] = key
        if evaluation.pk:evaluation.save(update_fields=["resultados", "input_snapshot", "updated_at"])
        else:evaluation.save(force_insert=True)
        AuditLog.objects.create(solicitud=s, event="CREDIHOY_RESULTADO_HDC_REAL_REGISTRADO",
            actor=f"user:{operador.pk}", request_id=str(op.request_id), payload={
                "operacionId": str(op.pk), "consultaId": str(op.consulta_id),
                "revisionEvidencia": revision_evidencia,
                "estadoTramite": "PENDIENTE_REVISION", "autorizadoDecisionCrediticia": False})
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
