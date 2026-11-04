import { validarResultadoShadow, type ResultadoShadow } from "./shadow";
import { validarEstadoLinix, type EstadoLinix } from "./linixEstado";
import { validarEstadoCredito, type EstadoCredito } from "./estadoCredito";

export type ClasificacionCapacidad = "CUMPLE_CAPACIDAD" | "NO_CUMPLE_CAPACIDAD" | "NO_EVALUABLE";
export interface ResultadoFuncional extends ResultadoShadow {
  versionFuncional: "credihoy-resultado-funcional-v1";
  resultadoCapacidad: { estado: ClasificacionCapacidad; motivos: string[]; referenciaPolitica: string | null };
  estadoTramite: "PENDIENTE_REVISION"; revisionHumanaHabilitada: false;
  secuencia: { paso: string; estado: string }[];
}
export interface EstadoFuncional {
  versionContrato: "credihoy-resultado-funcional-v1"; solicitudId: string; idempotencyKey: string;
  solicitud: { monto: string; plazoMeses: number; periodicidadPago: "MENSUAL" | null; recogerCredihoy: boolean | null };
  datosGuardados: boolean;
  consentimiento: { estado: "CONSENTIMIENTO_FIRMADO"; version: "CREDIHOY-REAL-1"; fechaFirma: string };
  estadoObtencionDatos: { fuente: "HDC" | "LINIX" | "POLITICA"; estado: string }[];
  resultado: ResultadoFuncional | null; estadoTramite: "DATOS_PENDIENTES" | "PENDIENTE_REVISION";
  decisionCrediticia: "NO_AUTORIZADA"; autorizadoDecisionCrediticia: false; radicacionLinix: "NO_EJECUTADA";
  estadoLinix?: EstadoLinix;
  estadoCredito?: EstadoCredito;
  accionesDisponibles?: { consultarCondiciones: { habilitada: boolean; motivo: string | null } };
}
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const fail = () => { throw new Error("CONTRATO_FUNCIONAL_INVALIDO"); };
const record = (x: unknown): Record<string, unknown> => x && typeof x === "object" && !Array.isArray(x) ? x as Record<string, unknown> : fail();
const texts = (x: unknown): x is string[] => Array.isArray(x) && x.length <= 100 && x.every(v => typeof v === "string" && v.length <= 256);
export function validarEstadoFuncional(value: unknown, id: string): EstadoFuncional {
  const p = record(value), s = record(p.solicitud), c = record(p.consentimiento);
  if (p.versionContrato !== "credihoy-resultado-funcional-v1" || p.solicitudId !== id || !uuid.test(id)
      || typeof p.idempotencyKey !== "string" || !uuid.test(p.idempotencyKey) || typeof p.datosGuardados !== "boolean"
      || p.decisionCrediticia !== "NO_AUTORIZADA" || p.autorizadoDecisionCrediticia !== false || p.radicacionLinix !== "NO_EJECUTADA"
      || !["DATOS_PENDIENTES", "PENDIENTE_REVISION"].includes(String(p.estadoTramite))
      || typeof s.monto !== "string" || !/^[0-9]{1,10}(?:\.[0-9]{1,2})?$/.test(s.monto)
      || !Number.isSafeInteger(s.plazoMeses) || Number(s.plazoMeses) < 1 || Number(s.plazoMeses) > 48
      || (s.periodicidadPago !== null && s.periodicidadPago !== "MENSUAL")
      || (s.recogerCredihoy !== null && typeof s.recogerCredihoy !== "boolean")
      || c.estado !== "CONSENTIMIENTO_FIRMADO" || c.version !== "CREDIHOY-REAL-1"
      || typeof c.fechaFirma !== "string" || !Number.isFinite(Date.parse(c.fechaFirma))) fail();
  if ((p.resultado === null ? p.estadoTramite !== "DATOS_PENDIENTES" : p.estadoTramite !== "PENDIENTE_REVISION")
      || (p.resultado !== null && p.datosGuardados !== true)
      || (p.datosGuardados && (s.periodicidadPago === null || s.recogerCredihoy === null))) fail();
  if (!Array.isArray(p.estadoObtencionDatos) || p.estadoObtencionDatos.length !== 3) fail();
  const estados = ["VIGENCIA_Y_APLICABILIDAD_NO_CERTIFICADAS", "INGRESO_COMPUTABLE_Y_HECHOS_GLOBALES_PENDIENTES", "CONDICION_Y_EFECTOS_CAPACIDAD_PENDIENTES"];
  const datos = (p.estadoObtencionDatos as unknown[]).map((raw, i) => {
    const d = record(raw);
    if (d.fuente !== ["HDC", "LINIX", "POLITICA"][i]
        || (d.estado !== estados[i] && d.estado !== "DISPONIBLE_EN_CONTEXTO_NO_APROBACION")) fail();
    return { fuente: d.fuente, estado: d.estado };
  });
  let resultado = null;
  if (p.resultado !== null) {
    const r = record(p.resultado), cap = record(r.resultadoCapacidad);
    if (r.versionFuncional !== p.versionContrato || r.solicitudId !== id || r.estadoTramite !== "PENDIENTE_REVISION"
        || r.revisionHumanaHabilitada !== false || !["CUMPLE_CAPACIDAD", "NO_CUMPLE_CAPACIDAD", "NO_EVALUABLE"].includes(String(cap.estado))
        || !texts(cap.motivos) || (cap.referenciaPolitica !== null && typeof cap.referenciaPolitica !== "string")
        || (cap.estado !== "NO_EVALUABLE" && (!cap.referenciaPolitica || record(r.capacidadMatematica).disponible !== true))
        || !Array.isArray(r.secuencia) || r.secuencia.length !== 6) fail();
    const fuente = r.fuenteHechos;
    if (fuente !== 'EXPEDIENTE_OPERATIVO_SIN_DECISION' && fuente !== 'CONSULTA_REAL_CONTROLADA') fail();
    const base = validarResultadoShadow(r, String(r.requestId), fuente as ResultadoShadow['fuenteHechos']);
    const valor = base.capacidadMatematica.valor;
    if (cap.estado === "CUMPLE_CAPACIDAD" && (valor === null || valor.startsWith("-") || !/[1-9]/.test(valor))) fail();
    const pasos = ["SOLICITUD", "DATOS", "CAPACIDAD", "REGLAS", "RESULTADO", "ESTADO"];
    const secuencia = (r.secuencia as unknown[]).map((raw, i) => {
      const h = record(raw);
      if (h.paso !== pasos[i] || typeof h.estado !== "string" || !/^[A-Z_]{1,80}$/.test(h.estado)) fail();
      return { paso: h.paso, estado: h.estado };
    });
    resultado = { ...base, versionFuncional: p.versionContrato, solicitudId: id,
      resultadoCapacidad: { estado: cap.estado, motivos: cap.motivos, referenciaPolitica: cap.referenciaPolitica },
      estadoTramite: "PENDIENTE_REVISION", revisionHumanaHabilitada: false, secuencia };
  }
  const estadoCredito = p.estadoCredito === undefined ? undefined : validarEstadoCredito(p.estadoCredito, id);
  let accionesDisponibles: EstadoFuncional["accionesDisponibles"];
  if (p.accionesDisponibles !== undefined) {
    const action = record(record(p.accionesDisponibles).consultarCondiciones);
    const motivos = ["PILOTO_EXISTENTE_SOLO_CONCILIACION", "EXPEDIENTE_CON_ENVIO_LINIX_SOLO_CONCILIACION", "CATALOGO_COMERCIAL_NO_HABILITADO"];
    if (typeof action.habilitada !== "boolean" || (action.habilitada ? action.motivo !== null : !motivos.includes(String(action.motivo)))) fail();
    accionesDisponibles = { consultarCondiciones: { habilitada: action.habilitada as boolean, motivo: action.motivo as string | null } };
  }
  if (estadoCredito && (Number(estadoCredito.asociado.resumen.monto) !== Number(s.monto)
      || estadoCredito.asociado.resumen.plazoMeses !== s.plazoMeses)) fail();
  return { versionContrato: p.versionContrato, solicitudId: id, idempotencyKey: p.idempotencyKey,
    solicitud: { monto: s.monto, plazoMeses: s.plazoMeses, periodicidadPago: s.periodicidadPago, recogerCredihoy: s.recogerCredihoy },
    datosGuardados: p.datosGuardados, consentimiento: { estado: c.estado, version: c.version, fechaFirma: c.fechaFirma },
    estadoObtencionDatos: datos, resultado, estadoTramite: p.estadoTramite,
    decisionCrediticia: "NO_AUTORIZADA", autorizadoDecisionCrediticia: false, radicacionLinix: "NO_EJECUTADA",
    ...(p.estadoLinix === undefined ? {} : { estadoLinix: validarEstadoLinix(p.estadoLinix, id) }),
    ...(estadoCredito === undefined ? {} : { estadoCredito }),
    ...(accionesDisponibles === undefined ? {} : { accionesDisponibles }) } as EstadoFuncional;
}
