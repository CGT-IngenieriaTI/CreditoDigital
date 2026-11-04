import { PASOS_CREDIHOY, PRODUCTO_CREDIHOY, type CredihoyState, type DatosSolicitante, type FinanzasDeclaradas,
  type SolicitudCredihoy, type ContextoCredihoy, type ConsultaObligaciones, type PrevisualizacionSolicitud,
  type SeguimientoCredihoy, type PasoCredihoy, type EnvioSolicitudCredihoy, type CondicionesProducto } from "./types";
import { normalizarDatos, validarDatos, validarFinanzas, validarSolicitud } from "./validation";

export function crearEstadoInicial(): CredihoyState {
  return { paso: "inicio", contexto: null, condicionesProducto: null, estadoSolicitud: "BORRADOR",
    datos: { tipoIdentificacion: "CC", identificacion: "", nombres: "", apellidos: "", celular: "", correo: "" },
    finanzas: { ingresos: "", otrosIngresos: "", gastos: "" },
    solicitud: { monto: "", plazoMeses: "", periodicidadPago: "MENSUAL", recogerCredihoy: false, obligacionRecogidaReferencia: null },
    consultaObligaciones: null, condiciones: null, seguimiento: null, consentimientos: {} };
}
export function documentosValidos(contexto: ContextoCredihoy): boolean {
  const docs = contexto.documentos;
  return docs.length > 0 && docs.some(d => d.requerido) && new Set(docs.map(d => d.id)).size === docs.length
    && docs.every(d => Boolean(d.id && d.titulo && d.version) && typeof d.requerido === "boolean"
      && (Boolean(d.contenido?.trim()) || urlDocumentoSegura(d.url) !== null));
}
export function urlDocumentoSegura(url: string | null): string | null {
  // Documentos servidos por el propio canal/backend, no URLs de terceros.
  return url && /^\/(?!\/)/.test(url) && !/[\\\s]/.test(url) ? url : null;
}
export function autorizacionesCompletas(state: CredihoyState): boolean {
  return Boolean(state.contexto && documentosValidos(state.contexto)
    && state.contexto.documentos.filter(d => d.requerido).every(d => state.consentimientos[d.id] === true));
}
export type CredihoyAction =
  | { type: "CONTEXTO"; contexto: ContextoCredihoy; condicionesProducto: CondicionesProducto; historico?: boolean }
  | { type: "DATOS"; datos: Partial<DatosSolicitante> }
  | { type: "FINANZAS"; finanzas: Partial<FinanzasDeclaradas> }
  | { type: "SOLICITUD"; solicitud: Partial<SolicitudCredihoy> }
  | { type: "OBLIGACIONES"; consulta: ConsultaObligaciones }
  | { type: "CONDICIONES"; condiciones: PrevisualizacionSolicitud }
  | { type: "CONSENTIMIENTO"; id: string; aceptado: boolean }
  | { type: "PASO"; paso: PasoCredihoy }
  | { type: "ENVIADA"; seguimiento: SeguimientoCredihoy }
  | { type: "SEGUIMIENTO"; seguimiento: SeguimientoCredihoy }
  | { type: "RETOMAR" }
  | { type: "REINICIAR" };
export function credihoyReducer(state: CredihoyState, action: CredihoyAction): CredihoyState {
  const invalidado = { condiciones: null, seguimiento: null, consentimientos: {}, estadoSolicitud: "BORRADOR" as const };
  switch (action.type) {
    case "CONTEXTO": return { ...state, contexto: action.contexto, condicionesProducto: action.condicionesProducto, modoHistorico: action.historico === true, consentimientos: {} };
    case "DATOS": return { ...state, ...invalidado, datos: { ...state.datos, ...action.datos }, consultaObligaciones: null,
      solicitud: { ...state.solicitud, recogerCredihoy: false, obligacionRecogidaReferencia: null } };
    case "FINANZAS": return { ...state, ...invalidado, finanzas: { ...state.finanzas, ...action.finanzas } };
    case "SOLICITUD": {
      const solicitud = { ...state.solicitud, ...action.solicitud };
      if (!solicitud.recogerCredihoy) solicitud.obligacionRecogidaReferencia = null;
      return { ...state, ...invalidado, solicitud };
    }
    case "OBLIGACIONES": return { ...state, consultaObligaciones: action.consulta };
    case "CONDICIONES": return { ...state, condiciones: action.condiciones };
    case "CONSENTIMIENTO": return state.contexto?.documentos.some(d => d.id === action.id)
      ? { ...state, consentimientos: { ...state.consentimientos, [action.id]: action.aceptado } } : state;
    case "PASO": return puedeIrAPaso(state, action.paso) ? { ...state, paso: action.paso } : state;
    case "ENVIADA": return { ...state, seguimiento: action.seguimiento, estadoSolicitud: action.seguimiento.estado, paso: "estado" };
    case "SEGUIMIENTO": return { ...state, seguimiento: action.seguimiento, estadoSolicitud: action.seguimiento.estado };
    case "RETOMAR": return state.estadoSolicitud === "BORRADOR" ? { ...state, ...invalidado, paso: "datos" } : state;
    case "REINICIAR": return { ...crearEstadoInicial(), modoHistorico: state.modoHistorico, contexto: state.contexto, condicionesProducto: state.condicionesProducto };
  }
}
export function puedeIrAPaso(state: CredihoyState, next: PasoCredihoy): boolean {
  const actual = PASOS_CREDIHOY.indexOf(state.paso), destino = PASOS_CREDIHOY.indexOf(next);
  if (state.paso === "estado") return false;
  if (destino <= actual) return true;
  if (destino !== actual + 1) return false;
  switch (state.paso) {
    case "inicio": return Boolean(state.contexto && state.condicionesProducto);
    case "datos": return !Object.keys(validarDatos(normalizarDatos(state.datos), state.modoHistorico)).length && state.consultaObligaciones !== null;
    case "financiero": return !Object.keys(validarFinanzas(state.finanzas, state.modoHistorico)).length;
    case "solicitud": return !Object.keys(validarSolicitud(state.solicitud, state.condicionesProducto, state.consultaObligaciones)).length && state.condiciones !== null;
    default: return false;
  }
}
export function pasoAnterior(paso: PasoCredihoy): PasoCredihoy { return PASOS_CREDIHOY[Math.max(0, PASOS_CREDIHOY.indexOf(paso) - 1)]; }
export function construirEnvio(state: CredihoyState, exigirAutorizaciones = true): EnvioSolicitudCredihoy {
  if (!state.contexto || !documentosValidos(state.contexto) || (exigirAutorizaciones && !autorizacionesCompletas(state))) {
    throw new Error("Autorizaciones incompletas.");
  }
  const datos = normalizarDatos(state.datos);
  if (Object.keys({ ...validarDatos(datos, state.modoHistorico), ...validarFinanzas(state.finanzas, state.modoHistorico),
    ...validarSolicitud(state.solicitud, state.condicionesProducto, state.consultaObligaciones) }).length) throw new Error("Solicitud inválida.");
  return { producto: PRODUCTO_CREDIHOY, solicitante: datos, finanzasDeclaradas: { ...state.finanzas },
    solicitud: { ...state.solicitud, plazoMeses: Number(state.solicitud.plazoMeses) },
    consentimientos: state.contexto.documentos.map(d => ({ id: d.id, version: d.version, aceptado: state.consentimientos[d.id] === true })) };
}
