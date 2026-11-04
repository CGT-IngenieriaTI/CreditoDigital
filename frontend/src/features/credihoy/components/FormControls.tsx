import { useLayoutEffect, useRef, type ChangeEvent, type ReactNode, type InputHTMLAttributes } from "react";
import type { ValorFinanciero } from "../types";
import { editarCop, formatoEdicionCop, moneda } from "../money";
export { moneda } from "../money";

function MoneyInput({ value, onValue, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & { value: string; onValue: (value: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const cursor = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (cursor.current === null || !input.current) return;
    const formatted = input.current.value;
    let index = 0, significativos = 0;
    while (index < formatted.length && significativos < cursor.current) {
      if (/[0-9,]/.test(formatted[index])) significativos++;
      index++;
    }
    input.current.setSelectionRange(index, index); cursor.current = null;
  }, [value]);
  return <div className="ch-money-input"><span aria-hidden="true">$</span><input {...props} ref={input} type="text" inputMode="decimal"
    value={formatoEdicionCop(value)} onKeyDown={e => {
      const element = e.currentTarget;
      const start = element.selectionStart ?? 0, end = element.selectionEnd ?? 0;
      if (start !== end) return;
      if (e.key === "Backspace" && element.value[start - 1] === ".") element.setSelectionRange(start - 1, start - 1);
      if (e.key === "Delete" && element.value[start] === ".") element.setSelectionRange(start + 1, start + 1);
    }} onBlur={() => { if (value.endsWith(".")) onValue(value.slice(0, -1)); }} onChange={e => {
      const raw = editarCop(e.target.value);
      if (raw === null) return;
      cursor.current = e.target.value.slice(0, e.target.selectionStart ?? 0).replace(/[^0-9,]/g, "").length;
      onValue(raw);
    }} /></div>;
}

export function valorFinanciero(valor?: ValorFinanciero): string {
  return valor?.disponible && valor.valor !== null ? moneda(valor.valor) : "Pendiente de validación";
}

interface FieldProps {
  name: string; label: string; value: string; onChange: (value: string) => void;
  error?: string; type?: string; inputMode?: "text" | "numeric" | "decimal" | "tel" | "email";
  autoComplete?: string; maxLength?: number; help?: string; children?: ReactNode; money?: boolean;
}

export function Field({ name, label, value, onChange, error, type = "text", inputMode, autoComplete, maxLength, help, children, money = false }: FieldProps) {
  const change = (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => onChange(event.target.value);
  const described = [help && `${name}-help`, error && `${name}-error`].filter(Boolean).join(" ") || undefined;
  return <div className="ch-field">
    <label className="form-label" htmlFor={`ch-${name}`}>{label}</label>
    {children ? <select id={`ch-${name}`} name={name} value={value} onChange={change}
      className={`form-select ${error ? "is-invalid" : ""}`} aria-invalid={Boolean(error)} aria-describedby={described}>{children}</select>
      : money ? <MoneyInput id={`ch-${name}`} name={name} value={value} onValue={onChange}
        autoComplete="off" className={`form-control ${error ? "is-invalid" : ""}`} aria-invalid={Boolean(error)} aria-describedby={described} />
      : <input id={`ch-${name}`} name={name} value={value} onChange={change} type={type} inputMode={inputMode}
        autoComplete={autoComplete} maxLength={maxLength} className={`form-control ${error ? "is-invalid" : ""}`}
        aria-invalid={Boolean(error)} aria-describedby={described} />}
    {help && <div className="ch-help" id={`${name}-help`}>{help}</div>}
    {error && <div className="invalid-feedback d-block" id={`${name}-error`}>{error}</div>}
  </div>;
}

export function Actions({ onBack, busy = false, label = "Continuar", disabled = false, busyLabel = "Un momento..." }: {
  onBack?: () => void; busy?: boolean; label?: string; disabled?: boolean; busyLabel?: string;
}) {
  return <div className="ch-actions">
    {onBack ? <button className="btn btn-outline-secondary" type="button" onClick={onBack} disabled={busy}>Anterior</button> : <span />}
    <button className="btn btn-brand" type="submit" disabled={busy || disabled}>
      {busy && <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />}{busy ? busyLabel : label}
    </button>
  </div>;
}
