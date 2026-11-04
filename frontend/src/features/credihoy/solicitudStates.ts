import type { EstadoSolicitudCredihoy } from "./types";

// Presentacion de estados autoritativos, no reglas de aprobacion/transicion crediticia.
export const PRESENTACION_ESTADOS: Record<EstadoSolicitudCredihoy, { titulo: string; tono: string; texto: string; icono: string; cta: string; accion: "RETOMAR" | "ACTUALIZAR" | "NUEVA" }> = {
  BORRADOR: { titulo: "Solicitud en borrador", tono: "neutral", texto: "Puedes continuar completando tu solicitud.", icono: "○", cta: "Continuar solicitud", accion: "RETOMAR" },
  RECIBIDA: { titulo: "Solicitud recibida", tono: "recibida", texto: "Hemos recibido tu solicitud para continuar su proceso.", icono: "✓", cta: "Actualizar estado", accion: "ACTUALIZAR" },
  EN_VALIDACION: { titulo: "En validación", tono: "validacion", texto: "Estamos revisando la información de tu solicitud.", icono: "◷", cta: "Actualizar estado", accion: "ACTUALIZAR" },
  REQUIERE_VALIDACION: { titulo: "Requiere validación", tono: "validacion", texto: "Tu solicitud necesita una revisión adicional.", icono: "!", cta: "Consultar estado", accion: "ACTUALIZAR" },
  APROBADA: { titulo: "Solicitud aprobada", tono: "aprobada", texto: "Consulta las condiciones informadas para continuar.", icono: "✓", cta: "Actualizar estado", accion: "ACTUALIZAR" },
  RECHAZADA: { titulo: "Solicitud no aprobada", tono: "rechazada", texto: "Consulta el detalle informado sobre tu solicitud.", icono: "×", cta: "Nueva solicitud", accion: "NUEVA" },
  ERROR_TECNICO: { titulo: "No pudimos actualizar tu solicitud", tono: "error", texto: "Esto no representa una decisión crediticia. Intenta consultar nuevamente.", icono: "↻", cta: "Reintentar consulta", accion: "ACTUALIZAR" },
};
