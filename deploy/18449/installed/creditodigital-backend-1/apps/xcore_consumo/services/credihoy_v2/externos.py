"""Proyeccion del JSON HDC detallado: sin XML, HTTP, defaults ni agregados."""

from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal

from .base import EstadoCertificacion as EC, Hecho
from .hechos import (
    AtributoOriginalHDCV2, CoberturaHDCV2, CuentaHDCV2, EstadoCuentaHDCV2, HechosExternosCrediHoyV2,
    ImporteHDCV2, ObservacionHDCV2,
)


ATRIBUTOS_CARACTERISTICAS = frozenset({
    "tipoCuenta", "descripcion", "descripcionTipoCuenta", "nombre", "nombreTipoCuenta",
    "calidadDeudor", "tipoObligacion", "tipoContrato", "ejecucionContrato", "garantia",
    "mesesPermanencia", "clase", "marca", "franquicia", "amparada", "codigoAmparada",
})
ATRIBUTOS_VALOR = frozenset({
    "calificacion", "cuotasCanceladas", "cuotasMora", "disponible", "fechaLimitePago",
    "fechaPagoCuota", "totalCuotas",
})


def _objeto(valor, ruta):
    if not isinstance(valor, dict):
        raise ValueError(f"{ruta}: requiere objeto")
    return valor


def _texto_original(valor, ruta):
    if valor is not None and not isinstance(valor, str):
        raise ValueError(f"{ruta}: requiere string o null")
    return valor


def normalizar_hdc_v2(payload: dict, *, referencia_evidencia: str,
                      fecha_consulta: datetime | None,
                      fecha_consulta_original: str | None = None) -> HechosExternosCrediHoyV2:
    """Acepta obligaciones[] del normalizado detallado, no historial_raw V1.

    La fecha recibida debe ser la consulta original, no updated_at. La cobertura
    permanece desconocida aunque todas las filas presentes tengan datos.
    Si la fuente no declara zona, fecha_consulta debe ser None y la fecha literal
    se entrega como fecha_consulta_original; no se construye un instante supuesto.
    """
    payload = _objeto(payload, "HDC")
    if payload.get("estado") == "ERROR" or payload.get("status") == "ERROR" or payload.get("Fault"):
        raise ValueError("HDC declara error; no es evidencia utilizable")
    filas = payload.get("obligaciones")
    if not isinstance(filas, list):
        raise ValueError("HDC.obligaciones requiere lista explicita")
    faltantes, ambiguas, cuentas = [], [], []
    referencias = defaultdict(list)
    tipos = Counter()

    def lista(objeto, clave, ruta):
        valor = objeto.get(clave)
        if valor is None:
            faltantes.append(f"{ruta}.{clave}")
            return []
        if not isinstance(valor, list):
            raise ValueError(f"{ruta}.{clave}: requiere lista")
        return valor

    def hecho(valor, ruta, *, semantica=False):
        if valor is None or valor == "":
            faltantes.append(ruta)
        # El string vacio se preserva como original, no como valor financiero cero.
        estado = EC.NO_DISPONIBLE if valor is None else (EC.SEMANTICA_PENDIENTE if semantica else EC.OBSERVADO)
        return Hecho(valor=valor, disponible=valor is not None, fuente="HDC",
                     referencia_evidencia=referencia_evidencia, ruta_origen=ruta,
                     fecha_consulta=fecha_consulta, estado_certificacion=estado)

    def texto_hecho(objeto, clave, ruta, *, semantica=False):
        path = f"{ruta}.{clave}"
        return hecho(_texto_original(objeto.get(clave), path), path, semantica=semantica)

    def atributos(objeto, claves, ruta):
        # Lista permitida, nunca copia generica de payload/XML/seguridad.
        originales = []
        for clave, valor in objeto.items():
            if clave not in claves:
                continue
            path = f"{ruta}.{clave}"
            if valor is not None and type(valor) not in (str, int, Decimal, bool):
                raise ValueError(f"{path}: requiere atributo original escalar; no float")
            originales.append(AtributoOriginalHDCV2(
                nombre=clave, hecho=hecho(valor, path, semantica=True)))
        return tuple(originales)

    for i, fila in enumerate(filas):
        ruta = f"obligaciones[{i}]"
        fila = _objeto(fila, ruta)
        ref = texto_hecho(fila, "referencia_local", ruta)
        tipo = texto_hecho(fila, "tipo", ruta)
        if ref.valor and ref.valor.strip():
            referencias[ref.valor].append(i)
        else:
            ambiguas.append(f"{ruta}.REFERENCIA_LOCAL_AUSENTE")
        ambiguas.append(f"{ruta}.IDENTIDAD_ENTRE_CONSULTAS_NO_CERTIFICADA")
        tipos[tipo.valor or "NO_INFORMADO"] += 1
        estados = []
        for j, estado in enumerate(lista(fila, "estados", ruta)):
            path = f"{ruta}.estados[{j}]"
            estado = _objeto(estado, path)
            estados.append(EstadoCuentaHDCV2(**{
                k: texto_hecho(estado, k, path, semantica=True)
                for k in ("tipo", "codigo", "fecha", "meses", "descripcion")},
                atributos_originales=atributos(estado, {"nombre"}, path),
                posicion_original=j, ruta_origen=path))
        observaciones = []
        valores = lista(fila, "valores", ruta)
        if not valores and f"{ruta}.valores" not in faltantes:
            faltantes.append(f"{ruta}.valores")
        for j, valor in enumerate(valores):
            path = f"{ruta}.valores[{j}]"
            valor = _objeto(valor, path)
            moneda = texto_hecho(valor, "moneda", path, semantica=True)
            unidad = texto_hecho(valor, "unidad", path, semantica=True)

            def importe(clave):
                original = valor.get(clave)
                if original is not None and type(original) not in (str, int, Decimal):
                    raise ValueError(f"{path}.{clave}: importe original invalido; no float")
                if original is None or original == "":
                    faltantes.append(f"{path}.{clave}")
                return ImporteHDCV2(valor_original=original, unidad_original=unidad.valor,
                    moneda_original=moneda.valor, estado_homologacion=(
                        EC.NO_DISPONIBLE if original is None else EC.SEMANTICA_PENDIENTE),
                    referencia_evidencia=referencia_evidencia, ruta_origen=f"{path}.{clave}")

            dias = valor.get("diasMora")
            if dias is not None and type(dias) not in (str, int):
                raise ValueError(f"{path}.diasMora: requiere string o int original")
            observaciones.append(ObservacionHDCV2(
                fecha=texto_hecho(valor, "fecha", path), moneda_original=moneda,
                unidad_original=unidad,
                periodicidad_original=texto_hecho(valor, "periodicidad", path, semantica=True),
                saldo_actual=importe("saldoActual"), saldo_mora=importe("saldoMora"),
                dias_mora=hecho(dias, f"{path}.diasMora", semantica=True), cuota=importe("cuota"),
                valor_inicial=importe("valorInicial"), cupo_inicial=importe("cupoInicial"),
                cupo_total=importe("cupoTotal"),
                atributos_originales=atributos(valor, ATRIBUTOS_VALOR, path),
                posicion_original=j, ruta_origen=path))
        embargo = fila.get("embargo")
        if embargo is not None and type(embargo) is not bool:
            raise ValueError(f"{ruta}.embargo: requiere bool o null explicito")
        caracteristicas = fila.get("caracteristicas")
        if caracteristicas is None:
            caracteristicas = {}
        caracteristicas = _objeto(caracteristicas, f"{ruta}.caracteristicas")
        cuentas.append(CuentaHDCV2(
            referencia_local=ref, tipo_cuenta=tipo,
            entidad_codigo=texto_hecho(fila, "entidad_codigo", ruta),
            rol_original=texto_hecho(caracteristicas, "calidadDeudor", f"{ruta}.caracteristicas", semantica=True),
            sector_codigo_original=texto_hecho(fila, "sector", ruta, semantica=True),
            embargo=hecho(embargo, f"{ruta}.embargo"),
            tipo_embargo=texto_hecho(fila, "tipo_embargo", ruta, semantica=True),
            estados_originales=tuple(estados), observaciones=tuple(observaciones),
            entidad=texto_hecho(fila, "entidad", ruta, semantica=True),
            subtipo_cuenta_original=texto_hecho(caracteristicas, "tipoCuenta", f"{ruta}.caracteristicas", semantica=True),
            caracteristicas_originales=atributos(caracteristicas, ATRIBUTOS_CARACTERISTICAS, f"{ruta}.caracteristicas"),
            fecha_apertura=texto_hecho(fila, "fecha_apertura", ruta, semantica=True),
            fecha_vencimiento=texto_hecho(fila, "fecha_vencimiento", ruta, semantica=True),
            fecha_actualizacion=texto_hecho(fila, "fecha_actualizacion", ruta, semantica=True),
            fecha_corte_original=texto_hecho(fila, "fecha_corte", ruta, semantica=True),
            posicion_original=i, ruta_origen=ruta))
    duplicados = tuple(tuple(indices) for indices in referencias.values() if len(indices) > 1)
    for indices in duplicados:
        ambiguas.extend(f"obligaciones[{i}].REFERENCIA_LOCAL_REPETIDA" for i in indices)
    return HechosExternosCrediHoyV2(referencia_evidencia=referencia_evidencia,
        fecha_consulta=fecha_consulta, fecha_consulta_original=fecha_consulta_original,
        cuentas=tuple(cuentas),
        cobertura_por_tipo=tuple(CoberturaHDCV2(tipo_cuenta=k, registros_observados=v) for k, v in tipos.items()),
        campos_faltantes=tuple(faltantes), identidades_ambiguas=tuple(ambiguas),
        posibles_duplicados=duplicados)
