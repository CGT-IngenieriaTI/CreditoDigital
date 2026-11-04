// COP: '.' agrupa miles, ',' separa decimales. Nunca convierte dinero a Number.
export function importeValido(valor: string): boolean { return /^[0-9]{1,18}(?:\.[0-9]{1,2})?$/.test(valor); }
export function editarCop(texto: string): string | null {
  if (texto === "") return "";
  if (!/^\$?\s*[0-9.]*,?[0-9]*$/.test(texto)) return null;
  const limpio = texto.replace(/^\$?\s*/, "").replace(/\./g, "");
  const [entero, decimal] = limpio.split(",");
  if (entero.length > 18 || (decimal?.length ?? 0) > 2) return null;
  if (!entero && decimal === undefined) return "";
  return `${entero || "0"}${decimal === undefined ? "" : "." + decimal}`;
}
export function formatoEdicionCop(valor: string): string {
  if (!valor) return "";
  const [entero, decimal] = valor.split(".");
  return `${entero.replace(/\B(?=(\d{3})+(?!\d))/g, ".")}${decimal === undefined ? "" : "," + decimal}`;
}
export function moneda(valor: string | null | undefined): string {
  if (valor == null || !/^-?[0-9]+(?:\.[0-9]+)?$/.test(valor)) return "Pendiente de validación";
  const [entero, fraccion] = valor.split(".");
  return `$ ${BigInt(entero).toLocaleString("es-CO")}${fraccion ? "," + fraccion : ""}`;
}
