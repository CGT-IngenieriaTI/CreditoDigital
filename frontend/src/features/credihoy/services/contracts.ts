import type { CondicionesProducto, ConsultaObligaciones, ContextoCredihoy, DatosSolicitante,
  EnvioSolicitudCredihoy, ErroresFormulario, PrevisualizacionSolicitud, SeguimientoCredihoy } from "../types";

// Un futuro ApiCredihoyAdapter implementara este port SOLO contra el backend propio.
// No son nombres de endpoints HTTP ni contratos de proveedores.
export interface CredihoyPort {
  readonly modo: "LOCAL" | "API";
  cargarContextoSolicitud(signal: AbortSignal): Promise<ContextoCredihoy>;
  cargarCondicionesProducto(signal: AbortSignal): Promise<CondicionesProducto>;
  consultarCredihoyVigente(datos: DatosSolicitante, signal: AbortSignal): Promise<ConsultaObligaciones>;
  previsualizarSolicitud(payload: EnvioSolicitudCredihoy, signal: AbortSignal): Promise<PrevisualizacionSolicitud>;
  enviarSolicitud(payload: EnvioSolicitudCredihoy, referenciaOperacion: string, signal: AbortSignal): Promise<SeguimientoCredihoy>;
  consultarEstado(referencia: string, signal: AbortSignal): Promise<SeguimientoCredihoy>;
}
export type CredihoyService = CredihoyPort;
export type TipoErrorCredihoy = "validation_error" | "technical_error" | "retryable_error";
export class ErrorCredihoy extends Error {
  constructor(readonly tipo: TipoErrorCredihoy, readonly campos: ErroresFormulario = {}, readonly codigo?: string) {
    super("Operacion CrediHoy no completada");
    this.name = "ErrorCredihoy";
  }
}

export function mensajeErrorCredihoy(error: unknown): string | undefined {
  if (!(error instanceof ErrorCredihoy)) return undefined;
  const mensajes: Record<string, string> = {
    IDENTIDAD_NO_AUTORIZADA: "La identificación y los apellidos no coinciden con el histórico autorizado. Revisa el paso Datos básicos; se admite el primer apellido o ambos cuando están presentes en la evidencia.",
    PARAMETROS_SOLICITUD_INVALIDOS: "Revisa el monto y el plazo de la solicitud. El monto admite hasta diez dígitos enteros y dos decimales.",
    CONSENTIMIENTO_REPRODUCCION_AUSENTE: "Debes aceptar las tres autorizaciones del ensayo histórico.",
    SESION_LABORATORIO_NO_VALIDA: "La sesión local o su protección CSRF no es válida. Inicia sesión nuevamente; no se cambiará la referencia de la operación.",
    OPERACION_REQUIERE_CONCILIACION: "Esta operación requiere conciliación: no se generará otra clave ni se creará otra solicitud para evitar el conflicto.",
    HISTORICO_MODIFICADO: "La evidencia histórica cambió. La evaluación se detuvo sin consultar proveedores.",
    FECHA_VERIFICADA_MODIFICADA: "El archivo privado de fecha verificada cambió. La evaluación se detuvo.",
    RESULTADO_HISTORICO_NO_ENCONTRADO: "No hay resultado persistido para esta referencia. Los datos personales del formulario no se conservan al recargar.",
  };
  return mensajes[error.codigo ?? ""];
}
