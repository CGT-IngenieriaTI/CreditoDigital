export const PRODUCTO_CREDIHOY = "CREDIHOY UNICO (NUEVO)" as const;
export const MODALIDAD_CREDIHOY = "111" as const;
export const PASOS_CREDIHOY = ["inicio", "datos", "financiero", "solicitud", "resumen", "estado"] as const;
export type PasoCredihoy = typeof PASOS_CREDIHOY[number];
export type ImporteDecimal = string;
export type ErroresFormulario = Record<string, string>;
export interface DatosSolicitante {
  tipoIdentificacion: "CC" | "CE" | "TI" | "PAS";
  identificacion: string; nombres: string; apellidos: string; celular: string; correo: string;
}
// Declarado por el asociado; nunca reemplaza informacion certificada de evaluacion.
export interface FinanzasDeclaradas { ingresos: ImporteDecimal; otrosIngresos: ImporteDecimal; gastos: ImporteDecimal }
export interface ValorFinanciero { disponible: boolean; valor: ImporteDecimal | null }
export interface ObligacionCredihoy {
  referencia: string | null; producto: string; modalidadCodigo: string | null;
  persona: "NATURAL" | "JURIDICA"; descripcion: string;
  cuotaMensual: ValorFinanciero; saldo: ValorFinanciero;
}
export type EstadoCredihoyVigente = "SIN_CREDIHOY" | "CREDIHOY_ELEGIBLE" | "CREDIHOY_NO_ELEGIBLE" | "PENDIENTE_CONSULTA";
export interface ConsultaObligaciones { estado: EstadoCredihoyVigente; obligaciones: readonly ObligacionCredihoy[] }
export interface CondicionesProducto {
  estado: "DOCUMENTADAS_LOCALMENTE" | "INFORMADAS_POR_BACKEND" | "PENDIENTE_BACKEND";
  plazoMinimoMeses: number | null; plazoMaximoMeses: number | null;
  montoMinimo: ImporteDecimal | null; montoMaximo: ImporteDecimal | null;
  periodicidades: readonly "MENSUAL"[];
}
export type ParametrosSolicitud = CondicionesProducto;
export interface CondicionesCredihoy {
  monto: ImporteDecimal | null; plazoMeses: number | null;
  cuota: ValorFinanciero; capacidad: ValorFinanciero;
}
export type PrevisualizacionSolicitud = CondicionesCredihoy;
export interface SolicitudCredihoy {
  monto: ImporteDecimal; plazoMeses: string; periodicidadPago: "MENSUAL";
  recogerCredihoy: boolean; obligacionRecogidaReferencia: string | null;
}
export interface DocumentoConsentimiento {
  id: string; titulo: string; version: string; requerido: boolean;
  contenido: string | null; url: string | null;
}
export interface Autorizacion extends DocumentoConsentimiento { aceptado: boolean }
export type Consentimientos = Record<string, boolean>;
export interface ContextoCredihoy { documentos: readonly DocumentoConsentimiento[] }
export const ESTADOS_SOLICITUD = ["BORRADOR", "RECIBIDA", "EN_VALIDACION", "REQUIERE_VALIDACION", "APROBADA", "RECHAZADA", "ERROR_TECNICO"] as const;
export type EstadoSolicitudCredihoy = typeof ESTADOS_SOLICITUD[number];
export interface SeguimientoCredihoy {
  referencia: string | null; estado: EstadoSolicitudCredihoy; fecha: string;
  mensaje: string; sintetico: boolean; autorizadoDecisionCrediticia: boolean;
}
export interface ResultadoEvaluacion { cuota: ValorFinanciero; capacidad: ValorFinanciero }
export interface EnvioSolicitudCredihoy {
  producto: typeof PRODUCTO_CREDIHOY;
  solicitante: DatosSolicitante; finanzasDeclaradas: FinanzasDeclaradas;
  solicitud: Omit<SolicitudCredihoy, "plazoMeses"> & { plazoMeses: number };
  consentimientos: readonly { id: string; version: string; aceptado: boolean }[];
}
export type EstadoIntegracion = "idle" | "loading" | "success" | "empty" | "validation_error" | "technical_error" | "retryable_error";
export interface IntegracionCredihoy { estado: EstadoIntegracion; errores?: ErroresFormulario; mensaje?: string }
export interface CredihoyState {
  modoHistorico?: boolean;
  paso: PasoCredihoy; contexto: ContextoCredihoy | null; condicionesProducto: CondicionesProducto | null;
  datos: DatosSolicitante; finanzas: FinanzasDeclaradas; solicitud: SolicitudCredihoy;
  consultaObligaciones: ConsultaObligaciones | null; condiciones: PrevisualizacionSolicitud | null;
  consentimientos: Consentimientos; seguimiento: SeguimientoCredihoy | null;
  estadoSolicitud: EstadoSolicitudCredihoy;
}
