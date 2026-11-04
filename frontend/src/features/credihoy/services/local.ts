import { ErrorCredihoy, type CredihoyPort } from "./contracts";
import type { EstadoCredihoyVigente, ObligacionCredihoy } from "../types";

export const DATOS_SINTETICOS = { tipoIdentificacion: "CC" as const, identificacion: "0000000000", nombres: "PERSONA DE PRUEBA",
  apellidos: "SIN IDENTIDAD REAL", celular: "0000000000", correo: "prueba@example.invalid" };
export const FINANZAS_SINTETICAS = { ingresos: "5000000", otrosIngresos: "0", gastos: "1250000" };
export interface OpcionesLocal {
  escenario?: EstadoCredihoyVigente;
  fallo?: "NINGUNO" | "CONTEXTO" | "CONSULTA" | "PREVISUALIZACION" | "ENVIO" | "ESTADO";
}
const obligacion = (): ObligacionCredihoy => ({ referencia: "LOCAL-OBLIGACION-FICTICIA", producto: "CREDIHOY",
  modalidadCodigo: "111", persona: "NATURAL", descripcion: "CrediHoy de prueba",
  cuotaMensual: { disponible: true, valor: "300000" }, saldo: { disponible: false, valor: null } });

export class LocalCredihoyAdapter implements CredihoyPort {
  readonly modo = "LOCAL" as const;
  private recibido = false;
  constructor(private readonly opciones: OpcionesLocal = {}) {
    if (!import.meta.env.DEV) throw new Error("Servicio local no disponible");
  }
  private async respuesta<T>(crear: () => T, signal: AbortSignal, operacion?: OpcionesLocal["fallo"]): Promise<T> {
    if (signal.aborted) throw new DOMException("Operacion cancelada", "AbortError");
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException("Operacion cancelada", "AbortError")); };
      const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 120);
      signal.addEventListener("abort", abort, { once: true });
    });
    if (signal.aborted) throw new DOMException("Operacion cancelada", "AbortError");
    if (operacion && this.opciones.fallo === operacion) throw new ErrorCredihoy("retryable_error");
    return crear();
  }
  cargarContextoSolicitud(signal: AbortSignal) {
    return this.respuesta(() => ({ documentos: [
      { id: "terminos", titulo: "Términos y condiciones", version: "PRUEBA-NO-CONTRACTUAL", requerido: true,
        contenido: "Documento sintético para revisar la presentación. No contiene términos legales ni constituye un contrato.", url: null },
      { id: "datos", titulo: "Tratamiento de datos personales", version: "PRUEBA-NO-CONTRACTUAL", requerido: true,
        contenido: "Documento sintético, sin política legal. La política oficial deberá ser suministrada antes de habilitar solicitudes reales.", url: null },
      { id: "consultas", titulo: "Autorización de consultas", version: "PRUEBA-NO-CONTRACTUAL", requerido: true,
        contenido: "Documento sintético. Su aceptación no realiza ni autoriza consultas reales en este recorrido local.", url: null },
    ] }), signal, "CONTEXTO");
  }
  cargarCondicionesProducto(signal: AbortSignal) {
    // Rango documentado en fases previas; NO reemplaza condiciones contractuales futuras.
    return this.respuesta(() => ({ estado: "DOCUMENTADAS_LOCALMENTE" as const, plazoMinimoMeses: 1, plazoMaximoMeses: 48,
      montoMinimo: null, montoMaximo: null, periodicidades: ["MENSUAL" as const] }), signal, "CONTEXTO");
  }
  consultarCredihoyVigente(_datos: unknown, signal: AbortSignal) {
    return this.respuesta(() => {
      const estado = this.opciones.escenario ?? "CREDIHOY_ELEGIBLE";
      return { estado, obligaciones: estado === "CREDIHOY_ELEGIBLE" || estado === "CREDIHOY_NO_ELEGIBLE" ? [obligacion()] : [] };
    }, signal, "CONSULTA");
  }
  previsualizarSolicitud(_payload: unknown, signal: AbortSignal) {
    return this.respuesta(() => ({ monto: null, plazoMeses: null, cuota: { disponible: false, valor: null },
      capacidad: { disponible: false, valor: null } }), signal, "PREVISUALIZACION");
  }
  private seguimiento() {
    return { referencia: "LOCAL-SOLICITUD-FICTICIA", estado: "RECIBIDA" as const, fecha: new Date().toISOString(),
      mensaje: "Tu solicitud ha sido recibida.", sintetico: true, autorizadoDecisionCrediticia: false };
  }
  enviarSolicitud(_payload: unknown, _referenciaOperacion: string, signal: AbortSignal) {
    return this.respuesta(() => { this.recibido = true; return this.seguimiento(); }, signal, "ENVIO");
  }
  consultarEstado(_referencia: string, signal: AbortSignal) {
    return this.respuesta(() => {
      if (!this.recibido) throw new ErrorCredihoy("technical_error");
      return this.seguimiento();
    }, signal, "ESTADO");
  }
}
export function crearServicioLocal(opciones?: OpcionesLocal): CredihoyPort { return new LocalCredihoyAdapter(opciones); }
