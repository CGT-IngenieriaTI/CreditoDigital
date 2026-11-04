export const camposReferencia = ["tipoReferencia", "nombre", "direccion", "ciudad", "ciudadDescripcion", "codigoCiudad",
  "codigoDepartamento", "codigoPais", "telefono", "email", "observacion", "parentesco", "tipoReferenciaDescripcion"] as const;
export type Referencia = Record<typeof camposReferencia[number], string>;
export interface ReferenciasCapturadas {
  versionContrato: "credihoy-referencias-captura-v1"; solicitudId: string; referencias: Referencia[];
  consentimiento: { version: "CREDIHOY-REFERENCIAS-1"; texto: string; aceptado: boolean };
  envioOracleHabilitado: false; estadoContrato3883: "PENDIENTE_VALIDACION"; requestId: string;
}
export interface CatalogosReferencias {
  versionContrato: "linix-referencias-catalogos-v1"; sha256: string; fechaConsulta: string;
  estadoContrato3883: "PENDIENTE_VALIDACION";
  tipos: { codigo: string; descripcion: string }[]; parentescos: { codigo: string; descripcion: string }[];
  paises: [string, string][]; departamentos: [string, string, string][]; ciudades: [string, string, string, string][];
  limites: Record<string, number>;
}
export function nuevaReferencia(): Referencia {
  return Object.fromEntries(camposReferencia.map(k => [k, ""])) as Referencia;
}
export function validarReferencias(p: unknown, id: string): ReferenciasCapturadas {
  const r = p as ReferenciasCapturadas;
  if (!r || r.versionContrato !== "credihoy-referencias-captura-v1" || r.solicitudId !== id
      || r.envioOracleHabilitado !== false || r.estadoContrato3883 !== "PENDIENTE_VALIDACION"
      || typeof r.requestId !== "string" || r.consentimiento?.version !== "CREDIHOY-REFERENCIAS-1"
      || typeof r.consentimiento.texto !== "string" || typeof r.consentimiento.aceptado !== "boolean"
      || !Array.isArray(r.referencias) || r.referencias.some(row => !row || Object.keys(row).length !== camposReferencia.length
        || camposReferencia.some(key => typeof row[key] !== "string"))) throw new Error("REFERENCIAS_RESPUESTA_INVALIDA");
  return r;
}
export function validarCatalogosReferencias(p: unknown): CatalogosReferencias {
  const r = p as CatalogosReferencias;
  if (!r || r.versionContrato !== "linix-referencias-catalogos-v1" || !/^[a-f0-9]{64}$/.test(r.sha256)
      || r.estadoContrato3883 !== "PENDIENTE_VALIDACION" || !r.limites
      || ["tipos", "parentescos", "paises", "departamentos", "ciudades"].some(key => !Array.isArray(r[key as keyof CatalogosReferencias]))) {
    throw new Error("REFERENCIAS_CATALOGO_INVALIDO");
  }
  return r;
}
