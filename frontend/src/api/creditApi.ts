import {
  AuthUser,
  AcceptedDocumentPayload,
  ConsumoOrchestrationSnapshot,
  ConsumoProcessConflict,
  ConsumoProcessResponse,
  ConsumoSolicitudResumen,
  ConsumoSolicitudStatus,
  FormularioBasico,
  LegalDocument,
  OtpChannel,
} from "../types/credit";

function resolveApiUrl() {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL;
  }
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000/api/v1`;
  }
  return "http://localhost:8000/api/v1";
}

const API_URL = resolveApiUrl();

let csrfToken = "";

function readCookie(name: string) {
  if (typeof document === "undefined") {
    return "";
  }
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : "";
}

function getCsrfToken() {
  const cookieToken = readCookie("csrftoken");
  if (cookieToken) {
    csrfToken = cookieToken;
  }
  return csrfToken;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isJsonBody = init.body && !(init.body instanceof FormData);
  const headers = new Headers(init.headers);

  if (isJsonBody) {
    headers.set("Content-Type", "application/json");
  }
  if (init.method && init.method !== "GET") {
    let token = getCsrfToken();
    if (!token) {
      await fetchCsrfToken();
      token = getCsrfToken();
    }
    headers.set("X-CSRFToken", token);
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = new Error(
      String(
        payload.detail ||
          payload.non_field_errors?.[0] ||
          Object.values(payload)[0] ||
          "Ocurrio un error inesperado."
      )
    ) as Error & { status?: number; payload?: unknown };
    error.status = response.status;
    error.payload = payload;
    throw error;
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export async function fetchCsrfToken() {
  const response = await fetch(`${API_URL}/csrf/`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  });
  const data = (await response.json()) as { csrfToken: string };
  csrfToken = readCookie("csrftoken") || data.csrfToken;
  return csrfToken;
}

export async function login(username: string, password: string) {
  const data = await request<{ user: AuthUser }>("/auth/login/", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  await fetchCsrfToken();
  return data;
}

export async function logout() {
  await request<void>("/auth/logout/", {
    method: "POST",
  });
  await fetchCsrfToken();
}

export function fetchMe() {
  return request<{ user: AuthUser }>("/auth/me/");
}

export function fetchLegalDocuments() {
  return request<LegalDocument[]>("/documentos/");
}

export function createConsumoSolicitud(payload: FormularioBasico) {
  return request<ConsumoSolicitudStatus>("/consumo/solicitudes/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function previewConsumoOrchestration(payload: FormularioBasico) {
  return request<ConsumoOrchestrationSnapshot>("/consumo/orquestacion/preview/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchConsumoSolicitudStatus(solicitudId: string) {
  return request<ConsumoSolicitudStatus>(`/consumo/solicitudes/${solicitudId}/`);
}

export function sendOtp(solicitudId: string, canal: OtpChannel) {
  return request<ConsumoSolicitudStatus>(`/consumo/solicitudes/${solicitudId}/otp/send/`, {
    method: "POST",
    body: JSON.stringify({ canal }),
  });
}

export function verifyOtp(solicitudId: string, codigo: string) {
  return request<ConsumoSolicitudStatus>(`/consumo/solicitudes/${solicitudId}/otp/verify/`, {
    method: "POST",
    body: JSON.stringify({ codigo }),
  });
}

export function submitConsent(
  solicitudId: string,
  payload: {
    accepted: boolean;
    version: string;
    canal: OtpChannel;
    text_snapshot: string;
    accepted_documents: AcceptedDocumentPayload[];
  }
) {
  return request<ConsumoSolicitudStatus>(`/consumo/solicitudes/${solicitudId}/consentimiento/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function consultConsumoCore(solicitudId: string, numero_identificacion?: string) {
  return request<ConsumoSolicitudStatus>(`/consumo/solicitudes/${solicitudId}/core/consultar/`, {
    method: "POST",
    body: JSON.stringify({ numero_identificacion }),
  });
}

export function saveConsumoForm(solicitudId: string, form_data: Record<string, unknown>) {
  return request<ConsumoSolicitudStatus>(`/consumo/solicitudes/${solicitudId}/formularios/xcore/`, {
    method: "POST",
    body: JSON.stringify({ form_data }),
  });
}

export async function processConsumoSolicitud(
  solicitudId: string,
  selected_hc2_keys?: string[]
): Promise<ConsumoProcessResponse | ConsumoProcessConflict> {
  try {
    return await request<ConsumoProcessResponse>(`/consumo/solicitudes/${solicitudId}/procesar/`, {
      method: "POST",
      body: JSON.stringify(selected_hc2_keys === undefined ? {} : { selected_hc2_keys }),
    });
  } catch (error) {
    const typed = error as Error & { status?: number; payload?: unknown };
    if (typed.status === 409 && typed.payload) {
      return typed.payload as ConsumoProcessConflict;
    }
    throw error;
  }
}

export function fetchConsumoDecision(solicitudId: string) {
  return request<ConsumoProcessResponse>(`/consumo/solicitudes/${solicitudId}/decision/`);
}

export function getConsumoDecisionPdfUrl(solicitudId: string) {
  return `${API_URL}/consumo/solicitudes/${solicitudId}/decision/pdf/`;
}


export function listConsumoSolicitudes(params?: {
  estado?: string;
  q?: string;
  date_from?: string;
  date_to?: string;
}) {
  const query = new URLSearchParams();
  if (params?.estado) query.set("estado", params.estado);
  if (params?.q) query.set("q", params.q);
  if (params?.date_from) query.set("date_from", params.date_from);
  if (params?.date_to) query.set("date_to", params.date_to);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request<ConsumoSolicitudResumen[]>(`/consumo/solicitudes/${suffix}`);
}

export function getConsumoDecisionPdfDownloadUrl(solicitudId: string) {
  return `${API_URL}/consumo/solicitudes/${solicitudId}/decision/pdf/?download=1`;
}

export async function downloadConsumoDecisionPdf(solicitudId: string, filename?: string) {
  const response = await fetch(getConsumoDecisionPdfDownloadUrl(solicitudId), {
    method: "GET",
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error("No fue posible descargar el PDF final.");
  }
  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || `${solicitudId}_consumo.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}
