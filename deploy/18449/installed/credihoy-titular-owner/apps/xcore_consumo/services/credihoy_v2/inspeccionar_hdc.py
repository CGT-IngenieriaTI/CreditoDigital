"""Inspeccion local explicita, sin Django, persistencia ni salida de evidencia."""

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re

from .externos import normalizar_hdc_v2


class ErrorInspeccion(ValueError):
    """Solo admite codigos publicos, nunca mensajes del payload/parser."""


def _objeto_unico(pares):
    objeto = {}
    for clave, valor in pares:
        if clave in objeto:
            raise ErrorInspeccion("JSON_CLAVES_DUPLICADAS")
        objeto[clave] = valor
    return objeto


def _constante_invalida(_):
    raise ErrorInspeccion("JSON_NUMERO_NO_FINITO")


def _preservacion(payload, dto):
    """Verifica todas las filas sin imprimir originales ni seleccionar muestras."""
    comprobaciones = []
    filas = payload["obligaciones"]
    comprobaciones.append(len(filas) == len(dto.cuentas))
    for fila, cuenta in zip(filas, dto.cuentas):
        for clave, destino in (
            ("referencia_local", "referencia_local"), ("tipo", "tipo_cuenta"),
            ("entidad_codigo", "entidad_codigo"), ("sector", "sector_codigo_original"),
            ("embargo", "embargo"), ("tipo_embargo", "tipo_embargo"),
        ):
            hecho = getattr(cuenta, destino)
            comprobaciones.extend((hecho.valor == fila.get(clave),
                                   hecho.disponible == (fila.get(clave) is not None)))
        rol = (fila.get("caracteristicas") or {}).get("calidadDeudor")
        comprobaciones.append(cuenta.rol_original.valor == rol)
        estados = fila.get("estados") or []
        comprobaciones.append(len(estados) == len(cuenta.estados_originales))
        for original, estado in zip(estados, cuenta.estados_originales):
            comprobaciones.extend(getattr(estado, k).valor == original.get(k)
                                  for k in ("tipo", "codigo", "fecha", "meses"))
        valores = fila.get("valores") or []
        comprobaciones.append(len(valores) == len(cuenta.observaciones))
        for valor, observacion in zip(valores, cuenta.observaciones):
            for clave, destino in (("fecha", "fecha"), ("moneda", "moneda_original"),
                                   ("unidad", "unidad_original"),
                                   ("periodicidad", "periodicidad_original"),
                                   ("diasMora", "dias_mora")):
                comprobaciones.append(getattr(observacion, destino).valor == valor.get(clave))
            for clave, destino in (("saldoActual", "saldo_actual"),
                                   ("saldoMora", "saldo_mora"), ("cuota", "cuota")):
                importe = getattr(observacion, destino)
                comprobaciones.extend((importe.valor_original == valor.get(clave),
                    importe.moneda_original == valor.get("moneda"),
                    importe.unidad_original == valor.get("unidad"),
                    importe.valor_normalizado is None))
    if not all(comprobaciones):
        raise ErrorInspeccion("PRESERVACION_HDC_INVALIDA")
    return len(comprobaciones)


def _tipo_publico(tipo):
    return tipo if tipo in ("CuentaAhorro", "TarjetaCredito", "CuentaCartera") else "TIPO_NO_RECONOCIDO"


def _codigo_publico(codigo):
    # Solo se publican codigos cortos, nunca etiquetas libres de la evidencia.
    return codigo if isinstance(codigo, str) and re.fullmatch(r"[0-9]{1,3}", codigo) else "CODIGO_NO_PUBLICABLE"


def _cobertura(cuentas):
    observaciones = [o for cuenta in cuentas for o in cuenta.observaciones]
    reporte = {
        "cuentas_totales": len(cuentas),
        "cuentas_con_sector": sum(c.sector_codigo_original.valor not in (None, "") for c in cuentas),
        "cuentas_sin_sector": sum(c.sector_codigo_original.valor in (None, "") for c in cuentas),
        "cuentas_con_estados": sum(bool(c.estados_originales) for c in cuentas),
        "cuentas_sin_estados": sum(not c.estados_originales for c in cuentas),
        "cuentas_sin_valores": sum(not c.observaciones for c in cuentas),
        "cuentas_con_embargo_explicito": sum(c.embargo.disponible for c in cuentas),
        "cuentas_sin_embargo_explicito": sum(not c.embargo.disponible for c in cuentas),
        "cuentas_con_cuota": sum(any(o.cuota.valor_original not in (None, "") for o in c.observaciones) for c in cuentas),
        "observaciones_totales": len(observaciones),
    }
    for campo in ("saldo_actual", "saldo_mora", "cuota"):
        reporte[f"observaciones_con_{campo}"] = sum(
            getattr(o, campo).valor_original not in (None, "") for o in observaciones)
    for campo, destino in (("dias_mora", "dias_mora"), ("periodicidad", "periodicidad_original"),
                           ("moneda", "moneda_original"), ("unidad", "unidad_original"), ("fecha", "fecha")):
        reporte[f"observaciones_con_{campo}"] = sum(
            getattr(o, destino).valor not in (None, "") for o in observaciones)
    con_cuota = [o for o in observaciones if o.cuota.valor_original not in (None, "")]
    reporte["cuotas_con_periodicidad"] = sum(o.periodicidad_original.valor not in (None, "") for o in con_cuota)
    reporte["cuotas_sin_periodicidad"] = len(con_cuota) - reporte["cuotas_con_periodicidad"]
    return reporte


def inspeccionar_payload_hdc(payload):
    """Devuelve solamente estadisticas; el DTO y los originales quedan en memoria."""
    try:
        original = payload["consulta"]["fecha_consulta"]
        if not isinstance(original, str) or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?", original):
            raise ValueError
        fecha = datetime.fromisoformat(original)
    except (KeyError, TypeError, ValueError):
        raise ErrorInspeccion("FECHA_CONSULTA_ORIGINAL_INVALIDA") from None
    con_zona = fecha.utcoffset() is not None
    try:
        dto = normalizar_hdc_v2(payload, referencia_evidencia="INSPECCION_LOCAL_NO_PERSISTIDA",
            fecha_consulta=fecha if con_zona else None, fecha_consulta_original=original)
        verificaciones = _preservacion(payload, dto)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ErrorInspeccion("ESTRUCTURA_HDC_V2_INVALIDA") from None
    reporte = _cobertura(dto.cuentas)
    tipos = Counter(_tipo_publico(c.tipo_cuenta.valor) for c in dto.cuentas)
    reporte.update({
        "version_contrato": dto.version_contrato,
        "fecha_consulta_zona": "OBSERVADA" if con_zona else "NO_CERTIFICADA",
        "fecha_original_preservada": dto.fecha_consulta_original == original,
        "cuentas_por_tipo": dict(sorted(tipos.items())),
        "cobertura_por_tipo": {tipo: _cobertura(tuple(
            c for c in dto.cuentas if _tipo_publico(c.tipo_cuenta.valor) == tipo)) for tipo in sorted(tipos)},
        "cobertura_completa": None,
        "sectores_originales": dict(sorted(Counter(
            _codigo_publico(c.sector_codigo_original.valor) for c in dto.cuentas
            if c.sector_codigo_original.valor not in (None, "")).items())),
        "periodicidades_originales_en_cuotas": dict(sorted(Counter(
            _codigo_publico(o.periodicidad_original.valor) for c in dto.cuentas for o in c.observaciones
            if o.cuota.valor_original not in (None, "") and o.periodicidad_original.valor not in (None, "")).items())),
        "referencias_duplicadas": len(dto.posibles_duplicados),
        "cuentas_en_grupos_duplicados": sum(len(g) for g in dto.posibles_duplicados),
        "campos_faltantes": dict(sorted(Counter(re.sub(r"\[[0-9]+\]", "[]", p)
            for p in dto.campos_faltantes).items())),
        "identidades_ambiguas": dict(sorted(Counter(p.rsplit(".", 1)[-1]
            for p in dto.identidades_ambiguas).items())),
        "preservacion_verificada": True,
        "comprobaciones_preservacion": verificaciones,
        "importes_normalizados": 0,
        "reconciliacion_linix": "NO_IMPLEMENTADA",
    })
    reporte["diagnosticos"] = ["MONEDA_UNIDAD_PERIODICIDAD_SECTOR_NO_HOMOLOGADOS",
                               "IDENTIDAD_ENTRE_CONSULTAS_NO_CERTIFICADA"]
    if reporte["cuentas_sin_embargo_explicito"]:
        reporte["diagnosticos"].append("EMBARGO_NO_HOMOLOGADO")
    if not con_zona:
        reporte["diagnosticos"].append("FECHA_CONSULTA_SIN_ZONA")
    return reporte


def inspeccionar_archivo_hdc(archivo):
    """Una ruta explicita, apertura binaria de lectura, sin escribir ningun archivo."""
    try:
        ruta = Path(archivo)
        with ruta.open("rb") as fuente:
            contenido = fuente.read(8 * 1024 * 1024 + 1)
        if len(contenido) > 8 * 1024 * 1024:
            raise ErrorInspeccion("ARCHIVO_EXCEDE_LIMITE")
        payload = json.loads(contenido.decode("utf-8-sig"), parse_float=Decimal,
            parse_constant=_constante_invalida, object_pairs_hook=_objeto_unico)
        reporte = inspeccionar_payload_hdc(payload)
        with ruta.open("rb") as fuente:
            posterior = fuente.read(8 * 1024 * 1024 + 1)
        if sha256(contenido).digest() != sha256(posterior).digest():
            raise ErrorInspeccion("ARCHIVO_CAMBIO_DURANTE_INSPECCION")
        reporte["archivo_sin_cambios"] = True
        return reporte
    except ErrorInspeccion:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ErrorInspeccion("ARCHIVO_HDC_NO_INSPECCIONABLE") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspeccion HDC V2 local de solo lectura")
    parser.add_argument("--archivo", required=True)
    argumentos = parser.parse_args(argv)
    try:
        reporte = inspeccionar_archivo_hdc(argumentos.archivo)
    except ErrorInspeccion as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, **reporte}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
