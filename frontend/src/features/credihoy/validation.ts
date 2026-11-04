import { MODALIDAD_CREDIHOY, type DatosSolicitante, type ErroresFormulario, type FinanzasDeclaradas,
  type ObligacionCredihoy, type CondicionesProducto, type SolicitudCredihoy, type ConsultaObligaciones } from "./types";
import { importeValido } from "./money";
export { importeValido } from "./money";

export function condicionesValidas(c: CondicionesProducto, permitirLocal = false): boolean {
  return ["DOCUMENTADAS_LOCALMENTE", "INFORMADAS_POR_BACKEND", "PENDIENTE_BACKEND"].includes(c.estado)
    && (permitirLocal || c.estado !== "DOCUMENTADAS_LOCALMENTE")
    && [c.plazoMinimoMeses, c.plazoMaximoMeses].every(v => v === null || (Number.isSafeInteger(v) && v > 0))
    && (c.plazoMinimoMeses === null || c.plazoMaximoMeses === null || c.plazoMinimoMeses <= c.plazoMaximoMeses)
    && [c.montoMinimo, c.montoMaximo].every(v => v === null || importeValido(v))
    && Array.isArray(c.periodicidades) && c.periodicidades.every(p => p === "MENSUAL");
}

function unidadesMenores(valor: string): bigint {
  const [entero, fraccion = ""] = valor.split(".");
  return BigInt(entero + fraccion.padEnd(2, "0"));
}
export function normalizarDatos(datos: DatosSolicitante): DatosSolicitante {
  return { ...datos, nombres: datos.nombres.trim(), apellidos: datos.apellidos.trim(), correo: datos.correo.trim() };
}
export function validarDatos(datos: DatosSolicitante, permitirAusentes = false): ErroresFormulario {
  const errores: ErroresFormulario = {};
  if (!["CC", "CE", "TI", "PAS"].includes(datos.tipoIdentificacion)) errores.tipoIdentificacion = "Selecciona un tipo de identificación.";
  if (!/^[0-9]{1,30}$/.test(datos.identificacion)) errores.identificacion = "Ingresa entre 1 y 30 dígitos.";
  for (const key of ["nombres", "apellidos"] as const) {
    if ((!permitirAusentes && !datos[key].trim()) || datos[key].length > 120) errores[key] = "Completa este campo, máximo 120 caracteres.";
  }
  // Formato de contacto; la validez/propiedad del numero corresponde al backend.
  if ((!permitirAusentes || datos.celular !== "") && !/^[0-9]{10}$/.test(datos.celular)) errores.celular = "Ingresa un celular de 10 dígitos.";
  if ((!permitirAusentes || datos.correo !== "") && (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(datos.correo) || datos.correo.length > 254)) errores.correo = "Ingresa un correo electrónico válido.";
  return errores;
}
export function validarFinanzas(finanzas: FinanzasDeclaradas, permitirAusentes = false): ErroresFormulario {
  return Object.fromEntries((["ingresos", "otrosIngresos", "gastos"] as const)
    .filter(key => !(permitirAusentes && finanzas[key] === "") && !importeValido(finanzas[key])).map(key => [key, "Ingresa un valor en pesos. Cero es un valor válido."]));
}
export function esCredihoyRecogible(o: ObligacionCredihoy): boolean {
  return o.producto === "CREDIHOY" && o.modalidadCodigo === MODALIDAD_CREDIHOY && o.persona === "NATURAL" && Boolean(o.referencia);
}
export function obligacionesElegibles(consulta: ConsultaObligaciones | null): readonly ObligacionCredihoy[] {
  return consulta?.estado === "CREDIHOY_ELEGIBLE" ? consulta.obligaciones.filter(esCredihoyRecogible) : [];
}
export function validarSolicitud(solicitud: SolicitudCredihoy, parametros: CondicionesProducto | null, consulta: ConsultaObligaciones | null): ErroresFormulario {
  const errores: ErroresFormulario = {};
  if (!importeValido(solicitud.monto) || unidadesMenores(solicitud.monto) <= 0n) errores.monto = "Ingresa un monto mayor que cero.";
  else if (parametros) {
    if (parametros.montoMinimo !== null && unidadesMenores(solicitud.monto) < unidadesMenores(parametros.montoMinimo)) errores.monto = "El monto está por debajo del límite informado.";
    if (parametros.montoMaximo !== null && unidadesMenores(solicitud.monto) > unidadesMenores(parametros.montoMaximo)) errores.monto = "El monto supera el límite informado.";
  }
  const plazo = Number(solicitud.plazoMeses);
  if (!parametros || parametros.plazoMinimoMeses === null || parametros.plazoMaximoMeses === null) {
    errores.plazoMeses = "Las condiciones del plazo están pendientes de confirmación.";
  } else if (!/^[0-9]+$/.test(solicitud.plazoMeses) || !Number.isSafeInteger(plazo)
    || plazo < parametros.plazoMinimoMeses || plazo > parametros.plazoMaximoMeses) {
    errores.plazoMeses = `Selecciona un plazo entre ${parametros.plazoMinimoMeses} y ${parametros.plazoMaximoMeses} meses.`;
  }
  if (!parametros?.periodicidades.includes(solicitud.periodicidadPago)) errores.periodicidadPago = "La periodicidad no está disponible.";
  if (solicitud.recogerCredihoy && !obligacionesElegibles(consulta).some(o => o.referencia === solicitud.obligacionRecogidaReferencia)) {
    errores.obligacionRecogidaReferencia = "Selecciona el CrediHoy disponible que deseas incluir.";
  }
  if (!solicitud.recogerCredihoy && solicitud.obligacionRecogidaReferencia !== null) errores.obligacionRecogidaReferencia = "Confirma si deseas incluir un CrediHoy.";
  return errores;
}
