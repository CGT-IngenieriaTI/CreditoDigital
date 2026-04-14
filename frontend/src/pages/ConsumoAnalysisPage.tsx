import { useEffect, useMemo, useState } from "react";

import { ConsumoProcessConflict, ConsumoSolicitudStatus, Hc2Obligacion } from "../types/credit";

const selectOptions = {
  tipo_cliente: ["ANTIGUO", "NUEVO"],
  tipo_credito: [
    "Libre inversion",
    "Libre inversion - Educacion",
    "Libre inversion - Empresas privadas",
    "Libre inversion - Oficiales",
    "Libre inversion - Pensionados",
    "Cesion de CDAT",
    "Cupo Rotativo",
    "Lineas Especiales",
  ],
  estrato: ["Nivel 1 y 2", "Nivel 3 y 4", "Nivel 5 y 6"],
  nivel_estudios: [
    "Tecnico o Tecnologico",
    "Especializacion, Maestria, Doctorado o Postdoctorado",
    "Ninguno, Primaria o Secundaria",
  ],
  estado_civil: ["Soltero", "Union Libre", "Casado", "Divorciado", "Viudo"],
  genero: ["Masculino", "Femenino"],
  tipo_vivienda: ["Propia", "Arriendo", "Familiar", "Otra"],
  forma_pago: ["Ventanilla", "Nomina"],
  garantia: ["Personal", "Codeudor o Fondo Garantías", "Otro"],
  tipo_contrato: [
    "Prestacion de servicios Formal",
    "Independiente",
    "Termino indefinido",
    "Pensionado",
    "Otro",
  ],
  numero_personas_cargo: ["0", "1", "2", "3", "4", "5"],
  edad: ["Menor de 31", "31-40", "41-50", "51-55", "55-60", "Mas de 60"],
  antiguedad_asociado: ["1 ano", "Entre 2 y 5 anos", "Entre 6 y 34 anos", "Mas de 34 anos"],
  ingresos: ["($1,500,000 - $3,000,000)", "($3,000,000 - $5,000,000)", "Mas de $5,000,000"],
  rango_score: ["Menor a 300", "300 - 500", "500 - 700", "700 - 800", "800 - 900", "Mas de 900"],
  aportes_sociales: [
    "($2,300,000 - $6,400,000)",
    "($6,400,000 - $12,800,000)",
    "($12,800,000 - $24,800,000)",
    "Mas de $24,800,000",
  ],
  activos: ["Menos de $150,000,000", "Mas de $150,000,000"],
  pasivos: ["SI", "NO"],
  ocupacion: [
    "Prestador de Servicios",
    "Pensionado",
    "Empleado",
    "Independiente",
    "Profesional Independiente",
    "Rentista de capital",
    "Transportador",
  ],
  canal: [
    "Principal",
    "Popular",
    "Acacias",
    "Porfia",
    "Montecarlo",
    "Granada",
    "Guayabetal",
    "Catama",
    "Barranca de Upia",
    "Puerto Gaitan",
    "Cabuyaro",
    "Vista Hermosa",
    "CB Cubarral",
    "CB Puerto Rico",
    "CB Lejanias",
    "CB Cumaral",
    "CB Villanueva",
    "CB Tauramena",
    "CB Yopal",
    "CB Puerto Lopez",
    "CB Mesetas",
    "CB Uribe",
    "CB El Castillo",
    "CB Puerto Lleras",
  ],
  zona: ["Rural", "Urbano"],
  tipo_garantia: ["FNG EMP319", "FNG EMP285", "FGA", "Codeudor", "Sin garantia", "Hipoteca", "Prenda", "Cesion de CDAT"],
} as const;

const textFields = [
  "valor_score",
  "valor_activos",
  "valor_pasivos",
  "valor_pasivos_recoge",
  "saldo_creditos",
  "cupos_tarjetas_rotativos",
  "tasa_cupos_rotativos",
  "asalariados",
  "pensionados",
  "prestadores_prof",
  "independientes",
  "rentistas_capital",
  "transportadores",
  "personas_cargo_ingresos",
  "cuotas_creditos_egresos",
  "cuotas_creditos_codeudor",
  "valor_cuotas_recoge_per",
  "valor_cuotas_recoge_nom",
  "otros_descuentos",
  "monto_solicitado",
  "plazo",
  "capitalizacion_aportes",
  "nombre",
] as const;

const numericFormattedFields = new Set([
  "valor_activos",
  "valor_pasivos",
  "valor_pasivos_recoge",
  "saldo_creditos",
  "cupos_tarjetas_rotativos",
  "asalariados",
  "pensionados",
  "prestadores_prof",
  "independientes",
  "rentistas_capital",
  "transportadores",
  "cuotas_creditos_egresos",
  "cuotas_creditos_codeudor",
  "valor_cuotas_recoge_per",
  "valor_cuotas_recoge_nom",
  "otros_descuentos",
  "monto_solicitado",
  "capitalizacion_aportes",
  "valor_score",
  "plazo",
]);


const hc2DerivedFields = new Set([
  "valor_pasivos",
  "valor_pasivos_recoge",
  "saldo_creditos",
  "cupos_tarjetas_rotativos",
  "tasa_cupos_rotativos",
  "cuotas_creditos_egresos",
  "cuotas_creditos_codeudor",
  "valor_cuotas_recoge_per",
  "valor_score",
  "rango_score",
]);


const labels: Record<string, string> = {
  tipo_cliente: "Tipo de asociado",
  tipo_credito: "Tipo de crédito",
  estrato: "Estrato",
  nivel_estudios: "Nivel de estudios",
  estado_civil: "Estado civil",
  genero: "Genero",
  tipo_vivienda: "Tipo de vivienda",
  forma_pago: "Forma de pago",
  garantia: "Garantía",
  tipo_contrato: "Tipo de contrato",
  numero_personas_cargo: "Numero de personas a cargo",
  edad: "Edad",
  antiguedad_asociado: "Antiguedad de asociado",
  ingresos: "Ingresos",
  rango_score: "Rango score",
  valor_score: "Score preliminar",
  aportes_sociales: "Aportes sociales",
  activos: "Activos",
  valor_activos: "Valor activos",
  pasivos: "Pasivos sector financiero/cooperativo",
  valor_pasivos: "Valor pasivos",
  valor_pasivos_recoge: "Pasivos que recoge",
  saldo_creditos: "Saldo total créditos",
  cupos_tarjetas_rotativos: "Cupos tarjetas + rotativos",
  tasa_cupos_rotativos: "Tasa cupos rotativos",
  ocupacion: "Ocupacion",
  canal: "Canal",
  zona: "Zona",
  tipo_garantia: "Tipo de garantia",
  asalariados: "Asalariados",
  pensionados: "Pensionados",
  prestadores_prof: "Prestadores de servicios / profesionales",
  independientes: "Independientes",
  rentistas_capital: "Rentistas de capital",
  transportadores: "Transportadores",
  personas_cargo_ingresos: "Personas a cargo (ingresos)",
  cuotas_creditos_egresos: "Total cuotas de créditos",
  cuotas_creditos_codeudor: "Cuotas de créditos como codeudor",
  valor_cuotas_recoge_per: "Cuota que recoge pago personal",
  valor_cuotas_recoge_nom: "Valor cuotas recoge nomina",
  otros_descuentos: "Otros descuentos",
  monto_solicitado: "Monto solicitado",
  plazo: "Plazo",
  capitalizacion_aportes: "Capitalizacion aportes",
  nombre: "Nombre completo",
};

const sectionConfig = [
  {
    key: "informacion_basica",
    title: "Informacion basica",
    copy: "Confirma la informacion base del asociado y completa solo lo que haga falta.",
    fields: [
      "nombre",
      "tipo_cliente",
      "estrato",
      "nivel_estudios",
      "estado_civil",
      "genero",
      "tipo_vivienda",
      "garantia",
      "tipo_contrato",
      "numero_personas_cargo",
      "edad",
      "antiguedad_asociado",
      "ocupacion",
      "tipo_credito",
      "forma_pago",
      "valor_score",
      "rango_score",
      "ingresos",
      "aportes_sociales",
      "activos",
      "valor_activos",
      "canal",
      "zona",
      "tipo_garantia",
    ],
  },
  {
    key: "ingresos",
    title: "Ingresos",
    copy: "Variables de ingresos y capacidad economica que alimentan el modelo.",
    fields: [
      "asalariados",
      "pensionados",
      "prestadores_prof",
      "independientes",
      "rentistas_capital",
      "transportadores",
      "personas_cargo_ingresos",
    ],
  },
  {
    key: "egresos",
    title: "Egresos",
    copy: "Obligaciones vigentes y compromisos financieros del asociado.",
    fields: [
      "pasivos",
      "valor_pasivos",
      "valor_pasivos_recoge",
      "saldo_creditos",
      "cupos_tarjetas_rotativos",
      "tasa_cupos_rotativos",
      "cuotas_creditos_egresos",
      "cuotas_creditos_codeudor",
      "valor_cuotas_recoge_per",
      "valor_cuotas_recoge_nom",
      "otros_descuentos",
    ],
  },
  {
    key: "solicitud_credito",
    title: "Informacion de la solicitud de crédito",
    copy: "Define la solicitud comercial que se enviara al motor XCORE.",
    fields: ["monto_solicitado", "plazo", "capitalizacion_aportes"],
  },
] as const;

const selectSynonyms: Record<string, Record<string, string>> = {
  tipo_cliente: { antiguo: "ANTIGUO", nuevo: "NUEVO" },
  forma_pago: { nomina: "Nomina", descuento: "Nomina", ventanilla: "Ventanilla" },
  genero: { m: "Masculino", masculino: "Masculino", hombre: "Masculino", f: "Femenino", femenino: "Femenino", mujer: "Femenino" },
  pasivos: { si: "SI", yes: "SI", true: "SI", no: "NO", false: "NO" },
  zona: { urbano: "Urbano", urbana: "Urbano", rural: "Rural" },
  tipo_vivienda: { propia: "Propia", propio: "Propia", arriendo: "Arriendo", alquilada: "Arriendo", familiar: "Familiar", otra: "Otra" },
  estado_civil: { soltero: "Soltero", soltera: "Soltero", casado: "Casado", casada: "Casado", divorciado: "Divorciado", divorciada: "Divorciado", viudo: "Viudo", viuda: "Viudo", unionlibre: "Union Libre", union_libre: "Union Libre", union: "Union Libre" },
};

function metricValue(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "Sin informacion";
  }
  return String(value);
}

function normalizeToken(value: string) {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, "")
    .toLowerCase();
}

function sameStringMap(left: Record<string, string>, right: Record<string, string>) {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }
  return leftKeys.every((key) => (left[key] ?? "") === (right[key] ?? ""));
}

function sameStringArray(left: string[], right: string[]) {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((value, index) => value === right[index]);
}

function normalizeSelectValue(name: string, rawValue: string) {
  const options = selectOptions[name as keyof typeof selectOptions] as readonly string[] | undefined;
  if (!options) {
    return rawValue;
  }
  const value = rawValue.trim();
  if (!value) {
    return "";
  }

  const normalized = normalizeToken(value);
  const directMatch = options.find((option) => normalizeToken(option) === normalized);
  if (directMatch) {
    return directMatch;
  }

  const synonymMatch = selectSynonyms[name]?.[normalized];
  if (synonymMatch) {
    return synonymMatch;
  }

  return value;
}

function normalizePercentageValue(rawValue: string) {
  const cleaned = rawValue.replace(/\s|%/g, "").replace(/,/g, ".");
  if (!cleaned) {
    return "";
  }
  let result = "";
  let separatorUsed = false;
  for (const char of cleaned) {
    if (/\d/.test(char)) {
      result += char;
      continue;
    }
    if (char === "." && !separatorUsed) {
      result += result ? "." : "0.";
      separatorUsed = true;
    }
  }
  if (!result) {
    return "";
  }
  const [integerPart, decimalPart = ""] = result.split(".");
  return decimalPart ? `${integerPart}.${decimalPart.slice(0, 4)}` : integerPart;
}

function formatPercentageDisplay(value: string) {
  const normalized = normalizePercentageValue(value);
  if (!normalized) {
    return "";
  }
  const [integerPart, decimalPart] = normalized.split(".");
  return decimalPart !== undefined ? `${integerPart}.${decimalPart}` : integerPart;
}

function normalizeFieldValue(name: string, value: unknown) {
  const stringValue = value === null || value === undefined ? "" : String(value).trim();
  if (!stringValue) {
    return "";
  }
  if (Object.prototype.hasOwnProperty.call(selectOptions, name)) {
    return normalizeSelectValue(name, stringValue);
  }
  if (name === "tasa_cupos_rotativos") {
    return normalizePercentageValue(stringValue);
  }
  if (numericFormattedFields.has(name)) {
    return stringValue.replace(/\D/g, "");
  }
  return stringValue;
}

function formatThousands(value: string) {
  const digits = value.replace(/\D/g, "");
  if (!digits) {
    return "";
  }
  return new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 }).format(Number(digits));
}

function formatInteger(value: number) {
  return new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 }).format(value || 0);
}

function deriveGarantia(tipoGarantia: string, currentGarantia: string) {
  const normalized = tipoGarantia.trim().toLowerCase();
  if (!normalized) return currentGarantia;
  if (normalized.includes("fng") || normalized == "fga" || normalized == "codeudor") {
    return "Codeudor o Fondo Garant\u00edas";
  }
  return currentGarantia;
}

function deriveFormaPago(tipoCredito: string) {
  const normalized = normalizeToken(tipoCredito);
  if (!normalized) {
    return "";
  }
  if (
    normalized === "libreinversion" ||
    normalized === "libreinversioneducacion" ||
    normalized === "cesiondecdat" ||
    normalized === "cuporotativo"
  ) {
    return "Ventanilla";
  }
  if (
    normalized === "libreinversionempresasprivadas" ||
    normalized === "libreinversionoficiales" ||
    normalized === "libreinversionpensionados"
  ) {
    return "Nomina";
  }
  return "";
}

function SelectField({
  name,
  value,
  options,
  onChange,
  disabled = false,
}: {
  name: string;
  value: string;
  options: readonly string[];
  onChange: (name: string, value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="col-md-6">
      <label className="form-label">{labels[name] ?? name}</label>
      <select className="form-select" value={value} disabled={disabled} onChange={(event) => onChange(name, event.target.value)}>
        <option value="">Seleccione una opcion</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </div>
  );
}

function InputField({
  name,
  value,
  onChange,
  disabled = false,
}: {
  name: string;
  value: string;
  onChange: (name: string, value: string) => void;
  disabled?: boolean;
}) {
  const isPercentage = name === "tasa_cupos_rotativos";
  const formattedValue = isPercentage
    ? formatPercentageDisplay(value)
    : numericFormattedFields.has(name)
    ? formatThousands(value)
    : value;
  const inputMode = isPercentage ? "decimal" : numericFormattedFields.has(name) ? "numeric" : undefined;

  return (
    <div className="col-md-6">
      <label className="form-label">{labels[name] ?? name}</label>
      <div className={isPercentage ? "input-group" : undefined}>
        <input
          className="form-control"
          inputMode={inputMode}
          value={formattedValue}
          disabled={disabled}
          readOnly={disabled}
          onChange={(event) => {
            const nextValue = event.target.value;
            if (disabled) {
              return;
            }
            if (name === "nombre") {
              onChange(name, nextValue);
              return;
            }
            if (isPercentage) {
              onChange(name, normalizePercentageValue(nextValue));
              return;
            }
            if (numericFormattedFields.has(name)) {
              onChange(name, nextValue.replace(/\D/g, ""));
              return;
            }
            onChange(name, nextValue);
          }}
        />
        {isPercentage ? <span className="input-group-text">%</span> : null}
      </div>
    </div>
  );
}

interface ConsumoAnalysisPageProps {
  solicitud: ConsumoSolicitudStatus;
  loadingCore: boolean;
  saving: boolean;
  processing: boolean;
  error: string;
  hc2Conflict: ConsumoProcessConflict | null;
  maskSensitiveData: boolean;
  onConsultCore: () => Promise<void>;
  onSave: (formData: Record<string, unknown>) => Promise<void>;
  onProcess: (formData: Record<string, unknown>, selectedKeys?: string[]) => Promise<void>;
}

export function ConsumoAnalysisPage({
  solicitud,
  loadingCore,
  saving,
  processing,
  error,
  hc2Conflict,
  maskSensitiveData,
  onConsultCore,
  onSave,
  onProcess,
}: ConsumoAnalysisPageProps) {
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);

  const orchestration = solicitud.orchestration;
  const consolidatedValues = orchestration?.valores_consolidados ?? {};
  const editableFields = orchestration?.campos_editables ?? [];
  const missingFields = orchestration?.campos_faltantes ?? [];
  const integrationErrors = orchestration?.integration_errors ?? {};
  const preselecta = orchestration?.datos_preselecta ?? {};
  const historial = orchestration?.historial_pago ?? {};
  const historialMetricsFormatted = ((hc2Conflict?.metrics_formatted_preview as Record<string, string> | undefined) ??
    (historial.metrics_formatted as Record<string, string> | undefined) ?? {}) as Record<string, string>;
  const historialMetrics = ((hc2Conflict?.metrics_preview as Record<string, number> | undefined) ??
    (historial.metrics as Record<string, number> | undefined) ?? {}) as Record<string, number>;
  const historialObligaciones = Array.isArray(historial.obligaciones_abiertas)
    ? (historial.obligaciones_abiertas as Hc2Obligacion[])
    : [];
  const hc2Obligaciones = hc2Conflict?.obligaciones_abiertas?.length
    ? hc2Conflict.obligaciones_abiertas
    : historialObligaciones;

  const eligibleObligations = useMemo(() => {
    return hc2Obligaciones
      .filter((item) => item.elegible_recoge)
      .filter((item) => !(Number(item.saldo_actual || 0) === 0 && Number(item.valor_cuota || 0) === 0))
      .sort((left, right) => {
        const leftZero = Number(left.saldo_actual || 0) === 0 && Number(left.valor_cuota || 0) === 0;
        const rightZero = Number(right.saldo_actual || 0) === 0 && Number(right.valor_cuota || 0) === 0;
        if (leftZero !== rightZero) {
          return leftZero ? 1 : -1;
        }
        return Number(right.saldo_actual || 0) - Number(left.saldo_actual || 0);
      });
  }, [hc2Obligaciones]);

  useEffect(() => {
    const persistedEntries = Object.entries(solicitud.form_data || {}).map(([key, value]) => [
      key,
      normalizeFieldValue(key, value),
    ]);
    const consolidatedEntries = Object.entries(consolidatedValues).map(([key, value]) => [
      key,
      normalizeFieldValue(key, value),
    ]);
    const merged = Object.fromEntries([...consolidatedEntries, ...persistedEntries]);
    merged.garantia = deriveGarantia(String(merged.tipo_garantia || ""), String(merged.garantia || ""));

    for (const [key, value] of consolidatedEntries) {
      if (!hc2DerivedFields.has(key)) {
        continue;
      }
      if (String(value ?? "").trim()) {
        merged[key] = value;
      }
    }

    const valorActivosConsolidado = consolidatedValues["valor_activos"];
    if (String(valorActivosConsolidado ?? "").trim()) {
      merged.valor_activos = normalizeFieldValue("valor_activos", valorActivosConsolidado);
    }

    setFormData((current) => (sameStringMap(current, merged) ? current : merged));
  }, [consolidatedValues, solicitud.form_data]);

  useEffect(() => {
    setSelectedKeys((current) => {
      if (!hc2Obligaciones.length) {
        return current.length ? [] : current;
      }
      const validCurrent = current.filter((key) => hc2Obligaciones.some((item) => item.key === key));
      if (validCurrent.length || current.length) {
        return sameStringArray(current, validCurrent) ? current : validCurrent;
      }
      const persisted = (solicitud.selected_hc2_keys ?? []).filter((key) =>
        hc2Obligaciones.some((item) => item.key === key)
      );
      return sameStringArray(current, persisted) ? current : persisted;
    });
  }, [hc2Obligaciones, solicitud.selected_hc2_keys]);


  const selectedObligations = useMemo(
    () => eligibleObligations.filter((item) => selectedKeys.includes(item.key)),
    [eligibleObligations, selectedKeys]
  );

  const selectionPreview = useMemo(() => {
    const pasivos = selectedObligations.reduce((acc, item) => acc + item.saldo_actual, 0);
    const cuota = selectedObligations.reduce((acc, item) => acc + item.valor_cuota, 0);
    const totalPasivos = eligibleObligations.reduce((acc, item) => acc + item.saldo_actual, 0);
    const totalCuota = eligibleObligations.reduce((acc, item) => acc + item.valor_cuota, 0);
    return { pasivos, cuota, totalPasivos, totalCuota };
  }, [eligibleObligations, selectedObligations]);

  useEffect(() => {
    if (!hc2Obligaciones.length) {
      return;
    }
    setFormData((current) => {
      const next = {
        ...current,
        valor_pasivos_recoge: String(selectionPreview.pasivos),
        valor_cuotas_recoge_per: String(selectionPreview.cuota),
      };
      return sameStringMap(current, next) ? current : next;
    });
  }, [hc2Obligaciones.length, selectionPreview.cuota, selectionPreview.pasivos]);

  const visibleFields = useMemo(() => {
    const withSavedValues = Object.entries(formData)
      .filter(([key, value]) => editableFields.includes(key) && String(value).trim())
      .map(([key]) => key);
    const visible = Array.from(new Set([...missingFields, ...withSavedValues]));
    const valorActivosOficial = String(consolidatedValues["valor_activos"] ?? "").trim();
    if (valorActivosOficial) {
      return visible.filter((field) => field !== "valor_activos");
    }
    return visible;
  }, [consolidatedValues, editableFields, formData, missingFields]);

  const refreshableIssues = useMemo(() => {
    const hasIntegrationErrors = Object.keys(integrationErrors).length > 0;
    const linixIncomplete = !String(consolidatedValues["nombre"] ?? "").trim();
    const preselectaIncomplete = !String(preselecta["decision"] ?? "").trim();
    const historialIncomplete = !String(historial.estado ?? "").trim() || !Object.keys(historialMetricsFormatted).length;
    return hasIntegrationErrors || linixIncomplete || preselectaIncomplete || historialIncomplete;
  }, [consolidatedValues, integrationErrors, preselecta, historial, historialMetricsFormatted]);

  const sectionedFields = useMemo(
    () =>
      sectionConfig
        .map((section) => ({
          ...section,
          fields: section.fields.filter((field) => visibleFields.includes(field)),
        }))
        .filter((section) => section.fields.length > 0),
    [visibleFields]
  );

  const hasFieldsToCapture = sectionedFields.length > 0;

  const financialHighlights = useMemo(() => {
    const rows: Array<[string, number]> = [
      ["Pasivos consolidados", Number(historialMetrics.valor_pasivos || 0)],
      ["Saldo total de créditos", Number(historialMetrics.saldo_total_creditos || 0)],
      ["Cupos rotativos", Number(historialMetrics.cupos_tarjetas_rotativos || 0)],
      ["Cuotas mensuales", Number(historialMetrics.total_cuotas_credito || 0)],
      [
        "Saldo deudor principal",
        Number(historialMetrics.saldo_total_creditos_deudor_principal || historialMetrics.saldo_total_creditos || 0),
      ],
      ["Saldo abierto como codeudor", Number(historialMetrics.saldo_abierto_codeudor || 0)],
      [
        "Cuotas deudor principal",
        Number(historialMetrics.total_cuotas_credito_deudor_principal || historialMetrics.total_cuotas_credito || 0),
      ],
      ["Cuotas como codeudor", Number(historialMetrics.cuota_abierta_codeudor || 0)],
    ];

    const hasFinancialData = rows.some(([, value]) => value > 0);
    if (!hasFinancialData) {
      return [] as Array<[string, string]>;
    }
    return rows.map(([label, value]) => [label, formatInteger(value)] as [string, string]);
  }, [historialMetrics]);

  const derivedFormaPago = deriveFormaPago(formData.tipo_credito ?? "");
  const preselectaAllowsContinue = Boolean(orchestration?.can_continue ?? preselecta?.puede_continuar);
  const showSensitivePanels = preselectaAllowsContinue && !maskSensitiveData;

  const handleChange = (name: string, value: string) => {
    setFormData((current) => {
      if (name === "tipo_credito") {
        const nextFormaPago = deriveFormaPago(value);
        return {
          ...current,
          [name]: value,
          forma_pago: nextFormaPago || current.forma_pago || "",
        };
      }
      if (name === "tipo_garantia") {
        return {
          ...current,
          [name]: value,
          garantia: deriveGarantia(value, current.garantia || ""),
        };
      }
      if (name === "cuotas_creditos_codeudor" || name === "tasa_cupos_rotativos" || name === "valor_activos") {
        return current;
      }
      return { ...current, [name]: value };
    });
  };

  return (
    <div className="row justify-content-center">
      <div className="col-12">
        <div className="content-card">
          <div className="d-flex flex-column flex-lg-row justify-content-between gap-3 mb-4">
            <div>
              <span className="eyebrow">Paso 3</span>
              <h2 className="section-title mt-2 mb-2">Formulario de solicitud</h2>
              <p className="section-copy mb-0">
                Revisa la informacion consolidada y completa unicamente los datos faltantes o comerciales para finalizar la solicitud de crédito.
              </p>
            </div>
            <div className="summary-chip">{solicitud.solicitud.numero_solicitud}</div>
          </div>

          <div className="readonly-note mb-4">
            <strong>Datos oficiales de LINIX.</strong> {orchestration?.nota_datos_oficiales}
          </div>

          {showSensitivePanels && financialHighlights.length > 0 ? (
            <section className="xcore-summary-panel mb-4">
              <div className="xcore-form-section__header">
                <h3 className="h5 mb-1">Resumen financiero consolidado</h3>
                <p className="section-copy section-copy--compact mb-0">
                  Estos valores se obtuvieron del historial interno y sirven como apoyo para el analisis del crédito.
                </p>
              </div>
              <div className="xcore-form-section__divider" />
              <div className="row g-3">
                {financialHighlights.map(([label, value]) => (
                  <div key={label} className="col-md-6 col-xl-3">
                    <div className="metric-card metric-card--readonly">
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {hasFieldsToCapture ? (
            <div className="d-grid gap-4">
              {sectionedFields.map((section) => {
                const selectFields = section.fields.filter((field) =>
                  Object.prototype.hasOwnProperty.call(selectOptions, field)
                );
                const inputFields = section.fields.filter((field): field is (typeof textFields)[number] =>
                  (textFields as readonly string[]).includes(field)
                );

                return (
                  <section key={section.key} className="xcore-form-section">
                    <div className="xcore-form-section__header">
                      <h3 className="h5 mb-1">{section.title}</h3>
                      <p className="section-copy section-copy--compact mb-0">{section.copy}</p>
                    </div>
                    <div className="xcore-form-section__divider" />
                    <div className="row g-3">
                      {(selectFields as Array<keyof typeof selectOptions>).map((name) => (
                        <SelectField
                          key={String(name)}
                          name={name}
                          value={name === "forma_pago" && derivedFormaPago ? derivedFormaPago : formData[name] ?? ""}
                          options={selectOptions[name]}
                          onChange={handleChange}
                          disabled={name === "forma_pago" && Boolean(derivedFormaPago)}
                        />
                      ))}
                      {inputFields.map((name) => (
                        <InputField
                          key={name}
                          name={name}
                          value={formData[name] ?? ""}
                          onChange={handleChange}
                          disabled={
                            name === "cuotas_creditos_codeudor" ||
                            name === "tasa_cupos_rotativos" ||
                            name === "valor_activos"
                          }
                        />
                      ))}
                    </div>
                  </section>
                );
              })}
            </div>
          ) : (
            <div className="xcore-form-section">
              <h3 className="h5 mb-2">Formulario listo</h3>
              <p className="section-copy mb-0">
                No quedan campos obligatorios por capturar. Puedes guardar avances o procesar la solicitud.
              </p>
            </div>
          )}

          {showSensitivePanels && eligibleObligations.length > 0 ? (
            <section className="xcore-form-section mt-4">
              <div className="xcore-form-section__header">
                <div>
                  <h3 className="h5 mb-1">Obligaciones elegibles para recoger</h3>
                  <p className="section-copy section-copy--compact mb-0">
                    Selecciona una, varias o todas las obligaciones abiertas que el nuevo crédito recogera. No se muestran telcos, tarjetas ni rotativos.
                  </p>
                </div>
                <div className="d-flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn btn-brand btn-sm"
                    onClick={() => setSelectedKeys(eligibleObligations.map((item) => item.key))}
                  >
                    Seleccionar todas
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline-primary btn-sm"
                    onClick={() => setSelectedKeys([])}
                  >
                    Limpiar seleccion
                  </button>
                </div>
              </div>
              <div className="xcore-form-section__divider" />
              <div className="row g-3 mb-3">
                <div className="col-md-6 col-xl-3">
                  <div className="metric-card metric-card--readonly">
                    <span>Obligaciones seleccionadas</span>
                    <strong>{selectedObligations.length} de {eligibleObligations.length}</strong>
                  </div>
                </div>
                <div className="col-md-6 col-xl-3">
                  <div className="metric-card metric-card--readonly">
                    <span>Pasivos que recoge</span>
                    <strong>{formatInteger(selectionPreview.pasivos)}</strong>
                  </div>
                </div>
                <div className="col-md-6 col-xl-3">
                  <div className="metric-card metric-card--readonly">
                    <span>Cuota que recoge pago personal</span>
                    <strong>{formatInteger(selectionPreview.cuota)}</strong>
                  </div>
                </div>
                <div className="col-md-6 col-xl-3">
                  <div className="metric-card metric-card--readonly">
                    <span>Maximo elegible</span>
                    <strong>{formatInteger(selectionPreview.totalPasivos)}</strong>
                  </div>
                </div>
              </div>
              <div className="table-responsive">
                <table className="table table-sm align-middle">
                  <thead>
                    <tr>
                      <th>Elegir</th>
                      <th>Entidad</th>
                      <th>Tipo</th>
                      <th>Numero</th>
                      <th>Rol</th>
                      <th>Estado</th>
                      <th>Saldo</th>
                      <th>Cuota</th>
                    </tr>
                  </thead>
                  <tbody>
                    {eligibleObligations.map((item) => (
                      <tr key={item.key}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selectedKeys.includes(item.key)}
                            onChange={(event) =>
                              setSelectedKeys((current) =>
                                event.target.checked
                                  ? [...current, item.key]
                                  : current.filter((key) => key !== item.key)
                              )
                            }
                          />
                        </td>
                        <td>{item.entidad}</td>
                        <td>{item.tipo_cuenta}</td>
                        <td>{item.numero_cuenta}</td>
                        <td>{item.rol ?? "Deudor principal"}</td>
                        <td>{item.estado_detalle ?? item.condicion}</td>
                        <td>{formatInteger(item.saldo_actual)}</td>
                        <td>{formatInteger(item.valor_cuota)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}

          <div className="xcore-actions mt-4">
            {refreshableIssues ? (
              <button type="button" className="btn btn-outline-primary" onClick={onConsultCore} disabled={loadingCore}>
                {loadingCore ? "Reintentando..." : "Reintentar consultas"}
              </button>
            ) : null}
            <button type="button" className="btn btn-outline-secondary" onClick={() => onSave(formData)} disabled={saving}>
              {saving ? "Guardando..." : "Guardar avances"}
            </button>
            <button
              type="button"
              className="btn btn-brand"
              onClick={() => onProcess(formData, hc2Obligaciones.length ? selectedKeys : undefined)}
              disabled={processing}
            >
              {processing ? "Procesando..." : "Procesar solicitud"}
            </button>
          </div>

          {error ? <div className="alert alert-danger mt-4 mb-0">{error}</div> : null}
        </div>
      </div>
    </div>
  );
}


