import type { FormEvent } from "react";
import { Actions, Field } from "../components/FormControls";
import type { ErroresFormulario, FinanzasDeclaradas } from "../types";

export function CredihoyFinancieroPage({ finanzas, onChange, onContinue, onBack, busy, errores, historico = false }: {
  finanzas: FinanzasDeclaradas; onChange: (value: Partial<FinanzasDeclaradas>) => void;
  historico?: boolean;
  onContinue: (event: FormEvent) => void; onBack: () => void; busy: boolean; errores: ErroresFormulario;
}) {
  return <form onSubmit={onContinue} noValidate>
    <div className="ch-note ch-note--neutral"><strong>Información declarada</strong><p>Estos valores no representan una capacidad de pago aprobada. La información se verificará durante la evaluación.</p></div>
    <div className="ch-section-heading"><h2>Ingresos mensuales</h2><p>Valores en pesos colombianos.</p></div>
    <div className="ch-fields">
      <Field name="ingresos" money label="Ingresos mensuales" value={finanzas.ingresos}
        onChange={value => onChange({ ingresos: value })} error={errores.ingresos} help="Valor de tus ingresos regulares." />
      <Field name="otrosIngresos" money label="Otros ingresos mensuales" value={finanzas.otrosIngresos}
        onChange={value => onChange({ otrosIngresos: value })} error={errores.otrosIngresos} help={historico ? "Opcional en el ensayo. Una ausencia no equivale a cero." : "Si no tienes otros ingresos, ingresa 0."} />
    </div>
    <div className="ch-section-heading ch-section-divider"><h2>Egresos y gastos mensuales</h2><p>Compromisos y gastos que declaras.</p></div>
    <div className="ch-fields">
      <Field name="gastos" money label="Egresos / gastos mensuales" value={finanzas.gastos}
        onChange={value => onChange({ gastos: value })} error={errores.gastos} />
    </div>
    <Actions onBack={onBack} busy={busy} />
  </form>;
}
