import { validarResultadoShadow } from "./shadow";
import { validarCotizacion } from "./cotizacion";
import { validarEstadoFuncional, type EstadoFuncional } from "./funcional";
import { validarReferencias, validarCatalogosReferencias, type Referencia } from "./referencias";

export interface EstadoFirmaReal {
  alcanceConsumo?: { proveedores: ["HDC"]; preselectaPermitida: false; endpointHdc: "/api/internal/v1/hdc/consultar/";
    productoLinix: { producto: "390"; modalidad: "111"; destinacion: "CFA" } };
  solicitudId: string; version: "CREDIHOY-REAL-1"; documento: string;
  estado: "PENDIENTE_ACEPTACION" | "OTP_PENDIENTE" | "OTP_VERIFICADO" | "CONSENTIMIENTO_FIRMADO";
  firmado: boolean; fechaFirma: string | null; canal: "SMS" | "EMAIL" | null;
  operacionEnCurso: boolean; consumoAutorizado: false;
  destinoEnmascarado?: string | null;
  otp: null | { estado: string; expiraAt: string | null; intentos: number; maxIntentos: number; expirada: boolean; reenvioEnSegundos: number };
}
export class ErrorFirmaReal extends Error {
  constructor(readonly codigo: string) { super("Operacion de consentimiento no completada"); }
}
export interface NuevoTitularCredihoy {
  clave: string; tipo_identificacion: string; numero_identificacion: string; primer_apellido: string;
  fecha_expedicion: string; celular: string; email: string; monto: string; plazo: number;
}
export interface PrecargaCredihoy {
  numeroDocumento: string; identificacionFuente: "REQUEST";
  versionContrato: "credihoy-precarga-piloto-v1"; referenciaPrecarga: string; fechaConsulta: string;
  fuente: "API_DATA.consultarSocio"; estado: "DATOS_PRECARGADOS";
  datos: { primerNombre: string | null; segundoNombre: string | null; primerApellido: string | null;
    segundoApellido: string | null; celular: string | null; fechaNacimiento: string | null; direccion: string | null;
    barrio: string | null; ciudad: string | null; estadoCivil: string | null; generoSocio: string | null;
    personasACargo: number | null; correo: string | null; fechaExpedicion: string | null };
  verificacionIdentidad: { metodo: "PRESENCIAL_OPERADOR_PILOTO"; identidadDigitalCertificada: false; referencia: string };
  camposFaltantes: string[]; autorizadoDecisionCrediticia: false; radicacionLinix: "NO_EJECUTADA";
}
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
export function solicitudFirmaReal(search: string) {
  const id = new URLSearchParams(search).get("firma-real");
  return id && uuid.test(id) ? id : null;
}
export class HttpConsentimientoReal {
  private csrf = "";
  private async json(path: string, method = "GET", body?: unknown, headers: Record<string, string> = {}) {
    if (method !== "GET" && !this.csrf) {
      const r = await this.json("/api/v1/csrf/") as { csrfToken: string };
      if (!/^[a-zA-Z0-9]{32,64}$/.test(r.csrfToken)) throw new ErrorFirmaReal("ERROR_TECNICO");
      this.csrf = r.csrfToken;
    }
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    try {
      const r = await fetch(path, { method, credentials: "same-origin", redirect: "error", cache: "no-store",
        signal: controller.signal, headers: { "Content-Type": "application/json", "X-CSRFToken": this.csrf, ...headers },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      if (r.status === 401 || r.status === 403) throw new ErrorFirmaReal("SESION_REQUERIDA");
      if (!r.ok) {
        const p = await r.json().catch(() => null);
        const allowed = ["OTP_NO_ENVIADA", "OTP_NO_VALIDADA", "OTP_REAL_REQUERIDA", "CONSENTIMIENTO_REAL_NO_HABILITADO",
          "OTP_REAL_VERIFICADA_REQUERIDA", "ACEPTACION_EXISTENTE_NO_SOBRESCRIBIR", "OTP_OPERACION_EN_CURSO_REQUIERE_RECUPERACION",
          "CAPTURA_TITULAR_NO_HABILITADA", "TITULAR_EXISTENTE_NO_SOBRESCRIBIR", "EXPEDIENTE_CONTROLADO_EXISTENTE_RECUPERAR",
          "CLAVE_PREPARACION_INCOMPATIBLE", "SOLICITUD_FIRMA_NO_AUTORIZADA"];
        allowed.push("PRECARGA_NO_HABILITADA", "VERIFICACION_PRESENCIAL_REQUERIDA", "ASOCIADO_NO_DISPONIBLE",
          "PRECARGA_NO_DISPONIBLE", "PRECARGA_EXPIRADA_O_NO_DISPONIBLE", "PRECARGA_NO_CORRESPONDE_AL_TITULAR");
        allowed.push("REFERENCIAS_CAPTURA_NO_HABILITADA", "REFERENCIAS_INVALIDAS", "REFERENCIAS_CATALOGO_INVALIDO",
          "REFERENCIAS_CONTACTO_INVALIDO", "REFERENCIAS_CONSENTIMIENTO_REQUERIDO", "CONSENTIMIENTO_FIRMADO_REQUERIDO",
          "EXPEDIENTE_CON_ENVIO_LINIX_NO_MODIFICABLE");
        throw new ErrorFirmaReal(allowed.includes(p?.codigo) ? p.codigo : "OPERACION_NO_COMPLETADA");
      }
      if (!r.headers.get("Content-Type")?.includes("application/json")) throw new ErrorFirmaReal("ERROR_TECNICO");
      return await r.json() as unknown;
    } catch (e) { throw e instanceof ErrorFirmaReal ? e : new ErrorFirmaReal("RESULTADO_DESCONOCIDO_RECUPERAR_ESTADO"); }
    finally { clearTimeout(timer); }
  }
  async login(username: string, password: string) {
    await this.json("/api/v1/auth/login/", "POST", { username, password });
    this.csrf = "";
  }
  private path(id: string, suffix: string) {
    if (!uuid.test(id)) throw new ErrorFirmaReal("ERROR_TECNICO");
    return `/api/v1/consumo/solicitudes/${id}/${suffix}/`;
  }
  async crear(origen: string, clave: string) {
    if (!uuid.test(origen) || !uuid.test(clave)) throw new ErrorFirmaReal("ERROR_TECNICO");
    const p = await this.json("/api/v1/consumo/solicitudes/", "POST", {
      modo: "CONSULTA_REAL_CONTROLADA", solicitud_origen: origen, clave }) as { solicitudId: string };
    if (!uuid.test(p.solicitudId)) throw new ErrorFirmaReal("ERROR_TECNICO");
    return p.solicitudId;
  }
  async crearTitular(titular: NuevoTitularCredihoy, precarga?: PrecargaCredihoy) {
    if (!uuid.test(titular.clave)) throw new ErrorFirmaReal("ERROR_TECNICO");
    const p = await this.json("/api/v1/consumo/solicitudes/", "POST", {
      modo: "CONSULTA_REAL_CONTROLADA", titular, ...(precarga ? {
        precargaReferencia: precarga.referenciaPrecarga, datosConfirmados: true } : {}) }) as { solicitudId: string };
    if (!p || p.solicitudId !== titular.clave) throw new ErrorFirmaReal("ERROR_TECNICO");
    return p.solicitudId;
  }
  private validarPrecarga(p: unknown): PrecargaCredihoy {
    const r = p as PrecargaCredihoy;
    if (!r || r.versionContrato !== "credihoy-precarga-piloto-v1" || !uuid.test(r.referenciaPrecarga)
        || !/^[0-9]{6,15}$/.test(r.numeroDocumento) || r.identificacionFuente !== "REQUEST"
        || r.fuente !== "API_DATA.consultarSocio" || r.estado !== "DATOS_PRECARGADOS" || !r.datos
        || r.verificacionIdentidad?.metodo !== "PRESENCIAL_OPERADOR_PILOTO"
        || r.verificacionIdentidad.identidadDigitalCertificada !== false
        || r.autorizadoDecisionCrediticia !== false || r.radicacionLinix !== "NO_EJECUTADA"
        || !Number.isFinite(Date.parse(r.fechaConsulta)) || !Array.isArray(r.camposFaltantes)
        || ["primerNombre", "segundoNombre", "primerApellido", "segundoApellido", "celular", "fechaNacimiento",
          "direccion", "barrio", "ciudad", "estadoCivil", "generoSocio"].some(k => {
            const v = r.datos[k as keyof PrecargaCredihoy["datos"]]; return v !== null && typeof v !== "string";
          }) || (r.datos.correo !== null && (typeof r.datos.correo !== "string"
            || r.datos.correo.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(r.datos.correo)))
        || (r.datos.fechaExpedicion !== null && (typeof r.datos.fechaExpedicion !== "string"
            || !/^\d{4}-\d{2}-\d{2}$/.test(r.datos.fechaExpedicion)
            || !Number.isFinite(Date.parse(r.datos.fechaExpedicion))
            || new Date(r.datos.fechaExpedicion).toISOString().slice(0, 10) !== r.datos.fechaExpedicion))
        || (r.datos.personasACargo !== null && (!Number.isSafeInteger(r.datos.personasACargo) || r.datos.personasACargo < 0))) {
      throw new ErrorFirmaReal("ERROR_TECNICO");
    }
    return r;
  }
  async precargar(numeroDocumento: string, referencia: string) {
    const requestId = crypto.randomUUID();
    const p = this.validarPrecarga(await this.json("/api/v1/credihoy/unico/precarga/", "POST", {
      numeroDocumento, identidadPresencial: { verificada: true, referencia } }, { "X-Request-ID": requestId }));
    if (p.numeroDocumento !== numeroDocumento || p.referenciaPrecarga !== requestId) throw new ErrorFirmaReal("ERROR_TECNICO");
    return p;
  }
  async recuperarPrecarga() { return this.validarPrecarga(await this.json("/api/v1/credihoy/unico/precarga/")); }
  private validar(p: unknown, id: string): EstadoFirmaReal {
    const r = p as EstadoFirmaReal;
    if (!r || r.solicitudId !== id || r.version !== "CREDIHOY-REAL-1" || typeof r.documento !== "string"
        || !["PENDIENTE_ACEPTACION", "OTP_PENDIENTE", "OTP_VERIFICADO", "CONSENTIMIENTO_FIRMADO"].includes(r.estado)
        || typeof r.firmado !== "boolean" || r.firmado !== (r.estado === "CONSENTIMIENTO_FIRMADO")
        || typeof r.operacionEnCurso !== "boolean" || r.consumoAutorizado !== false) throw new ErrorFirmaReal("ERROR_TECNICO");
    return r;
  }
  async estado(id: string) { return this.validar(await this.json(this.path(id, "consentimiento")), id); }
  private pathReferencias(id: string) {
    if (!uuid.test(id)) throw new ErrorFirmaReal("ERROR_TECNICO");
    return `/api/v1/credihoy/unico/solicitudes/${id}/referencias/`;
  }
  async referencias(id: string) {
    return validarReferencias(await this.json(this.pathReferencias(id)), id);
  }
  async catalogosReferencias(id: string) {
    return validarCatalogosReferencias(await this.json(this.pathReferencias(id) + "catalogos/"));
  }
  async guardarReferencias(id: string, referencias: Referencia[], aceptado: boolean) {
    return validarReferencias(await this.json(this.pathReferencias(id), "POST", {
      referencias, consentimiento: { version: "CREDIHOY-REFERENCIAS-1", aceptado }
    }, { "X-Request-ID": crypto.randomUUID() }), id);
  }
  private pathFuncional(id: string) {
    if (!uuid.test(id)) throw new ErrorFirmaReal("ERROR_TECNICO");
    return `/api/v1/credihoy/unico/solicitudes/${id}/evaluacion/`;
  }
  async estadoFuncional(id: string) { return validarEstadoFuncional(await this.json(this.pathFuncional(id)), id); }
  async cotizacion(id: string, ejecutar = false, requestId = crypto.randomUUID()) {
    if (!uuid.test(id) || !uuid.test(requestId)) throw new ErrorFirmaReal("ERROR_TECNICO");
    const p = validarCotizacion(await this.json(`/api/v1/credihoy/unico/solicitudes/${id}/cotizacion/`,
      ejecutar ? "POST" : "GET", ejecutar ? {} : undefined, { "X-Request-ID": requestId }), id);
    if (p.requestId !== requestId) throw new ErrorFirmaReal("ERROR_TECNICO");
    return p;
  }
  async simularCuota(id: string, escenario?: import('./cotizacion').EscenarioCotizacion, requestId = crypto.randomUUID()) {
    if (!uuid.test(id) || !uuid.test(requestId)) throw new ErrorFirmaReal('ERROR_TECNICO');
    const path = `/api/v1/credihoy/unico/solicitudes/${id}/cotizacion/`;
    const p = validarCotizacion(await this.json(path + (escenario ? '' : '?simulacion=1'),
      escenario ? 'POST' : 'GET', escenario ? { escenario } : undefined, { 'X-Request-ID': requestId }), id);
    if (p.requestId !== requestId) throw new ErrorFirmaReal('ERROR_TECNICO');
    return p;
  }
  async consultarCondiciones(id: string, requestId: string) {
    if (!uuid.test(requestId)) throw new ErrorFirmaReal("ERROR_TECNICO");
    return validarEstadoFuncional(await this.json(this.pathFuncional(id), "POST", { accion: "consultar_condiciones", datos: {} },
      { "Idempotency-Key": requestId, "X-Request-ID": requestId }), id);
  }
  async registrarDatosFuncionales(id: string, estado: EstadoFuncional, datos: unknown) {
    return validarEstadoFuncional(await this.json(this.pathFuncional(id), "POST", { accion: "guardar_datos", datos },
      { "Idempotency-Key": estado.idempotencyKey, "X-Request-ID": crypto.randomUUID() }), id);
  }
  async evaluarFuncional(id: string, estado: EstadoFuncional) {
    return validarEstadoFuncional(await this.json(this.pathFuncional(id), "POST", { accion: "evaluar", datos: {} },
      { "Idempotency-Key": estado.idempotencyKey, "X-Request-ID": crypto.randomUUID() }), id);
  }
  async revisionHistorica() {
    const p = await this.json("/api/v1/credihoy/unico/shadow/evaluar/?revision=historica") as { requestId: string };
    const result = validarResultadoShadow(p, p.requestId, "HISTORICO_CONTROLADO");
    if (!result.revisionHistorica) throw new ErrorFirmaReal("ERROR_TECNICO");
    return result;
  }
  async aceptar(id: string, canal: "SMS" | "EMAIL", destino: string) {
    return this.validar(await this.json(this.path(id, "consentimiento"), "POST", {
      accion: "aceptar", aceptado: true, contacto_aceptado: true, canal, destino }), id);
  }
  async enviar(id: string, canal: "SMS" | "EMAIL") {
    return this.validar(await this.json(this.path(id, "otp/send"), "POST", { canal, confirmacion_envio: true }), id);
  }
  async verificar(id: string, codigo: string) {
    return this.validar(await this.json(this.path(id, "otp/verify"), "POST", { codigo }), id);
  }
  async firmar(id: string) {
    return this.validar(await this.json(this.path(id, "consentimiento"), "POST", { accion: "firmar", aceptado: true }), id);
  }
}
