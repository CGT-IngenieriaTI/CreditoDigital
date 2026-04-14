export type StepKey = "formulario" | "otp" | "consentimiento" | "analisis" | "resultado";

export type OtpChannel = "SMS" | "EMAIL";

export interface AuthUser {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  full_name: string;
  email: string;
  role: string;
}

export interface FormularioBasico {
  tipo_identificacion: "CC" | "CE" | "TI" | "PAS";
  numero_identificacion: string;
  primer_apellido: string;
  fecha_expedicion: string;
  celular: string;
  email: string;
}

export interface DuplicateRequestPreview {
  has_active: boolean;
  solicitud_id: string;
  numero_solicitud: string;
  estado: string;
  asesor_nombre: string;
  is_incomplete: boolean;
  resume_available?: boolean;
  message: string;
}

export interface CreditoDigitalValidationPreview {
  ok: boolean;
  message: string;
  failed_attempts: number;
  remaining_attempts: number;
  blocked: boolean;
}

export interface CoreLookupPreview {
  found: boolean;
  message: string;
  data: Record<string, unknown>;
}

export interface ConsultaFormDefaults {
  nombre: string;
  fecha_expedicion: string;
  celular: string;
  email: string;
  primer_apellido: string;
}

export interface ConsumoOrchestrationSnapshot {
  duplicate_request?: DuplicateRequestPreview;
  validation_credito_digital?: CreditoDigitalValidationPreview;
  core?: CoreLookupPreview;
  form_defaults?: ConsultaFormDefaults;
  can_continue?: boolean;
  datos_linix?: Record<string, unknown>;
  datos_datacredito?: Record<string, unknown>;
  datos_preselecta?: Record<string, unknown>;
  historial_pago?: Record<string, unknown>;
  datos_basicos?: Partial<Record<keyof Omit<FormularioBasico, "tipo_identificacion" | "numero_identificacion">, string>>;
  campos_basicos_faltantes?: Array<keyof Omit<FormularioBasico, "tipo_identificacion" | "numero_identificacion">>;
  campos_basicos_bloqueados?: string[];
  valores_consolidados?: Record<string, unknown>;
  campos_editables?: string[];
  campos_bloqueados?: string[];
  campos_faltantes?: string[];
  nota_datos_oficiales?: string;
  integration_errors?: Record<string, string>;
}

export interface SolicitanteResumen {
  tipo_identificacion: string;
  numero_identificacion: string;
  primer_apellido: string;
  celular: string;
  email: string;
}

export interface LegalDocument {
  id: number;
  codigo: string;
  tipo_documento: string;
  titulo: string;
  descripcion: string;
  version: string;
  orden: number;
  pdf_url: string;
}

export interface AcceptedDocumentPayload {
  document_id: number;
  viewed_seconds: number;
  reached_end: boolean;
}

export interface AdvisorSummary {
  id: number;
  username: string;
  full_name: string;
  role: string;
}

export interface OtpStatus {
  canal: OtpChannel;
  destino: string;
  estado: string;
  enviado_at?: string | null;
  expira_at?: string | null;
  verificado_at?: string | null;
  intentos: number;
  max_intentos: number;
  ultimo_error: string;
  resend_available_in_seconds: number;
  debug_code?: string | null;
}

export interface ConsentStatus {
  version: string;
  aceptado: boolean;
  firmado: boolean;
  canal: OtpChannel;
  fecha_aceptacion: string;
  ip_address?: string | null;
  user_agent: string;
  text_hash: string;
  tipo_firma: string;
  otp_verified_at?: string | null;
}

export interface ConsentCopy {
  version: string;
  summary: string;
}

export interface Hc2Obligacion {
  key: string;
  source: string;
  tipo_cuenta: string;
  entidad: string;
  numero_cuenta: string;
  saldo_actual: number;
  valor_cuota: number;
  valor_inicial: number;
  condicion: string;
  rol?: string;
  estado_detalle?: string;
  elegible_recoge: boolean;
  motivo_no_elegible: string;
}

export interface ConsumoDecisionSnapshot {
  resultado: string;
  mensaje: string;
  monto_aprobado?: string;
  plazo_aprobado?: number;
  tasa_interes?: string;
}

export interface ConsumoEvaluacion {
  puntaje_xcore: number;
  perfil_riesgo: string;
  perfil_credito: string;
  capacidad_pago_final: string;
  decision_final: string;
  estamento: string;
  tiene_novedad: boolean;
  novedad_descripcion: string;
  monto_max_posible?: string;
  valor_cuota?: string;
  vida_deudores?: string;
  resultados: Record<string, unknown>;
  created_at: string;
}

export interface ConsumoSolicitudStatus {
  solicitud: {
    id: string;
    numero_solicitud: string;
    estado: string;
    paso_actual: string;
    producto: string;
    ultimo_error: string;
    created_at: string;
    updated_at: string;
    solicitante: SolicitanteResumen;
    asesor: AdvisorSummary | null;
    decision: {
      resultado: string;
      mensaje: string;
      monto_aprobado?: string;
    } | null;
  };
  estado: string;
  wizard_step: StepKey;
  oracle_consultado: boolean;
  documentos_autorizados: boolean;
  selected_hc2_keys: string[];
  core_data: Record<string, unknown>;
  form_data: Record<string, unknown>;
  ultimo_error: string;
  otp: OtpStatus | null;
  consentimiento: ConsentStatus | null;
  consent_copy: ConsentCopy;
  orchestration: ConsumoOrchestrationSnapshot;
  evaluacion: ConsumoEvaluacion | null;
  created_at: string;
  updated_at: string;
}

export interface ConsumoProcessResponse extends ConsumoEvaluacion {
  decision: ConsumoDecisionSnapshot;
  pdf_url: string;
  pdf_base64?: string;
}

export interface ConsumoProcessConflict {
  requires_hc2_selection: true;
  obligaciones_abiertas: Hc2Obligacion[];
  metrics_preview: Record<string, number>;
  metrics_formatted_preview: Record<string, string>;
}


export interface ConsumoSolicitudResumen {
  id: string;
  numero_solicitud: string;
  created_at: string;
  updated_at: string;
  estado: string;
  decision_final: string;
  pdf_url: string;
  numero_identificacion: string;
  primer_apellido: string;
  canal: string;
  agencia: string;
}

export interface DecisionFinal {
  numero_solicitud: string;
  resultado: "APROBADO" | "RECHAZADO" | "REVISION";
  mensaje: string;
  observaciones: string;
  monto_aprobado?: string;
  plazo_aprobado?: number;
  tasa_interes?: string;
  detalle?: Record<string, unknown>;
  created_at: string;
  solicitante: SolicitanteResumen;
}

