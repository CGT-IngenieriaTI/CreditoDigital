import type { FormEvent } from "react";
import { Actions, Field } from "../components/FormControls";
import type { DatosSolicitante, ErroresFormulario } from "../types";

export function CredihoyDatosPage({ datos, onChange, onContinue, onBack, busy, errores }: {
  datos: DatosSolicitante; onChange: (value: Partial<DatosSolicitante>) => void;
  onContinue: (event: FormEvent) => void; onBack: () => void; busy: boolean; errores: ErroresFormulario;
}) {
  return <form onSubmit={onContinue} noValidate>
    <div className="ch-section-heading"><h2>Identificación</h2><p>Información del solicitante.</p></div>
    <div className="ch-fields">
      <Field name="tipoIdentificacion" label="Tipo de identificación" value={datos.tipoIdentificacion}
        onChange={value => onChange({ tipoIdentificacion: value as DatosSolicitante["tipoIdentificacion"] })} error={errores.tipoIdentificacion}>
        <option value="CC">Cédula de ciudadanía</option><option value="CE">Cédula de extranjería</option>
        <option value="TI">Tarjeta de identidad</option><option value="PAS">Pasaporte</option>
      </Field>
      <Field name="identificacion" label="Número de identificación" inputMode="numeric" maxLength={30}
        value={datos.identificacion} onChange={value => onChange({ identificacion: value.replace(/[^0-9]/g, "") })} error={errores.identificacion} />
      <Field name="nombres" label="Nombres" autoComplete="given-name" maxLength={120}
        value={datos.nombres} onChange={value => onChange({ nombres: value })} error={errores.nombres} />
      <Field name="apellidos" label="Apellidos" autoComplete="family-name" maxLength={120}
        value={datos.apellidos} onChange={value => onChange({ apellidos: value })} error={errores.apellidos} />
    </div>
    <div className="ch-section-heading ch-section-divider"><h2>Datos de contacto</h2><p>Para mantenerte al tanto de tu solicitud.</p></div>
    <div className="ch-fields">
      <Field name="celular" label="Celular" inputMode="tel" autoComplete="tel-national" maxLength={10}
        value={datos.celular} onChange={value => onChange({ celular: value.replace(/[^0-9]/g, "") })} error={errores.celular} help="Número colombiano de 10 dígitos." />
      <Field name="correo" label="Correo electrónico" type="email" autoComplete="email" maxLength={254}
        value={datos.correo} onChange={value => onChange({ correo: value })} error={errores.correo} />
    </div>
    <Actions onBack={onBack} busy={busy} />
  </form>;
}
