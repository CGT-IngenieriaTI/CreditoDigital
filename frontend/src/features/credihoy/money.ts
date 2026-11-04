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
  const negativo=valor.startsWith('-');
  const [entero, fraccion=''] = valor.replace(/^-/, '').split('.');
  let centavos=BigInt(entero)*100n+BigInt((fraccion+'00').slice(0,2));
  if(fraccion.length>2&&fraccion[2]>='5')centavos+=1n;
  const unidades=(centavos/100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g,'.');
  return `$ ${negativo&&centavos!==0n?'-':''}${unidades},${(centavos%100n).toString().padStart(2,'0')}`;
}
