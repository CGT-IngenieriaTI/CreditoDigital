import base64
import math
import unicodedata
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import numpy_financial as npf
from django.core.exceptions import ObjectDoesNotExist
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from scipy.stats import weibull_min

try:
    from pypdf import PdfReader, PdfWriter
except Exception:  # pragma: no cover - fallback for alternate envs
    try:
        from PyPDF2 import PdfReader, PdfWriter
    except Exception:  # pragma: no cover - no template merge support
        PdfReader = None
        PdfWriter = None

from apps.xcore_consumo.models import (
    ConfiguracionAgenciaCanal,
    ConfiguracionGastosFamiliares,
    ConfiguracionRegresion,
    TasaInteresConsumo,
)

SMMLV_CONSUMO = 1750905.0
IVA = 0.19
INTERCEPTO = 1.8520

_FNG_EMP319_RAW = """
12 2,99
13 3,01
14 3,03
15 3,05
16 3,06
17 3,08
18 3,10
19 3,11
20 3,13
21 3,15
22 3,17
23 3,18
24 3,20
25 3,22
26 3,23
27 3,25
28 3,27
29 3,29
30 3,30
31 3,32
32 3,34
33 3,35
34 3,37
35 3,39
36 3,41
37 3,42
38 3,44
39 3,46
40 3,47
41 3,49
42 3,51
43 3,53
44 3,54
45 3,56
46 3,58
47 3,59
48 3,61
49 3,63
50 3,65
51 3,66
52 3,68
53 3,70
54 3,71
55 3,73
56 3,75
57 3,77
58 3,78
59 3,80
60 3,82
"""

_FNG_EMP285_RAW = """
12 2,50
13 2,52
14 2,56
15 2,63
16 2,71
17 2,81
18 2,92
19 3,04
20 3,17
21 3,31
22 3,46
23 3,61
24 3,76
25 3,82
26 3,89
27 3,97
28 4,06
29 4,16
30 4,26
31 4,38
32 4,49
33 4,62
34 4,75
35 4,88
36 5,02
37 5,09
38 5,17
39 5,26
40 5,35
41 5,45
42 5,55
43 5,66
44 5,77
45 5,89
46 6,01
47 6,13
48 6,26
49 6,34
50 6,42
51 6,51
52 6,60
53 6,70
54 6,80
55 6,91
56 7,01
57 7,13
58 7,24
59 7,36
60 7,48
61 7,56
62 7,64
63 7,73
64 7,82
65 7,92
66 8,02
67 8,12
68 8,22
69 8,33
70 8,44
71 8,55
72 8,66
73 8,75
74 8,83
75 8,92
76 9,01
77 9,10
78 9,20
79 9,29
80 9,39
81 9,50
82 9,60
83 9,71
84 9,81
85 9,89
86 9,98
87 10,06
88 10,15
89 10,24
90 10,33
91 10,43
92 10,52
93 10,62
94 10,72
95 10,82
96 10,92
97 11,00
98 11,08
99 11,16
100 11,25
101 11,33
102 11,42
103 11,51
104 11,60
105 11,69
106 11,79
107 11,88
108 11,98
109 12,05
110 12,13
111 12,21
112 12,29
113 12,37
114 12,46
115 12,54
116 12,63
117 12,71
118 12,80
119 12,89
120 12,98
"""


def normaliza(texto):
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return texto.strip().lower()


def _normalize_lookup_value(texto):
    if not texto:
        return ""
    normalized = normaliza(texto)
    normalized = " ".join(normalized.split())
    return normalized


def to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_rate_table(raw):
    table = {}
    for line in raw.strip().splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        plazo = int(parts[0])
        tasa = float(parts[1].replace("%", "").replace(",", "."))
        table[plazo] = tasa
    return table


FNG_EMP319_RATES = parse_rate_table(_FNG_EMP319_RAW)
FNG_EMP285_RATES = parse_rate_table(_FNG_EMP285_RAW)


def categoria_riesgo(perfil_riesgo):
    norm = normaliza(perfil_riesgo)
    if norm.endswith("a"):
        return "A"
    if norm.endswith("b"):
        return "B"
    if norm.endswith("c"):
        return "C"
    if norm.endswith("d"):
        return "D"
    return ""


def calcular_comision_garantia(tipo_garantia, tipo_cliente, forma_pago, perfil_riesgo, monto_solicitado, plazo, tipo_credito):
    garantia_norm = normaliza(tipo_garantia)
    tipo_cliente_norm = normaliza(tipo_cliente)
    forma_pago_norm = normaliza(forma_pago)
    categoria = categoria_riesgo(perfil_riesgo)
    monto = to_float(monto_solicitado)
    plazo_int = int(to_float(plazo))
    monto_smmlv = monto / SMMLV_CONSUMO if SMMLV_CONSUMO else 0
    tipo_credito_norm = normaliza(tipo_credito)
    res = {
        "comision_tipo": "",
        "comision_tasa_base": 0.0,
        "comision_tasa_total": 0.0,
        "comision_valor": 0.0,
        "comision_iva_valor": 0.0,
        "comision_total_valor": 0.0,
    }
    if garantia_norm == "fga":
        base = None
        if tipo_cliente_norm == "antiguo":
            if categoria in {"A", "B"}:
                base = 4.0 if forma_pago_norm == "ventanilla" else 2.0 if forma_pago_norm == "nomina" else None
            elif categoria in {"C", "D"}:
                base = 6.0 if forma_pago_norm == "ventanilla" else 3.0 if forma_pago_norm == "nomina" else None
        elif tipo_cliente_norm == "nuevo":
            if categoria == "A":
                base = 5.0 if forma_pago_norm == "ventanilla" else 2.0 if forma_pago_norm == "nomina" else None
            elif categoria == "B":
                base = 6.0 if forma_pago_norm == "ventanilla" else 2.0 if forma_pago_norm == "nomina" else None
            elif categoria in {"C", "D"}:
                base = 8.0 if forma_pago_norm == "ventanilla" else 3.5 if forma_pago_norm == "nomina" else None
        if base is None:
            return {**res, "error": "No se pudo calcular FGA con los valores suministrados."}
        valor_base = monto * (base / 100)
        valor_iva = valor_base * IVA
        return {
            "comision_tipo": "FGA",
            "comision_tasa_base": round(base, 4),
            "comision_tasa_total": round(base * (1 + IVA), 4),
            "comision_valor": round(valor_base, 2),
            "comision_iva_valor": round(valor_iva, 2),
            "comision_total_valor": round(valor_base + valor_iva, 2),
        }
    if garantia_norm in {"fng emp319", "fng epm 319"}:
        if plazo_int < 12 or plazo_int > 60:
            return {**res, "error": "FNG EMP319 permite plazo entre 12 y 60 meses."}
        if monto_smmlv < 1 or monto_smmlv > 6:
            return {**res, "error": "FNG EMP319 permite monto entre 1 y 6 SMMLV."}
        tasa = FNG_EMP319_RATES.get(plazo_int)
        if tasa is None:
            return {**res, "error": f"No hay tasa FNG EMP319 para plazo {plazo_int}."}
        valor_base = monto * (tasa / 100)
        valor_iva = valor_base * IVA
        return {
            "comision_tipo": "FNG EMP319",
            "comision_tasa_base": round(tasa, 4),
            "comision_tasa_total": round(tasa * (1 + IVA), 4),
            "comision_valor": round(valor_base, 2),
            "comision_iva_valor": round(valor_iva, 2),
            "comision_total_valor": round(valor_base + valor_iva, 2),
        }
    if garantia_norm in {"fng emp285", "fng epm 285"}:
        if plazo_int < 12 or plazo_int > 120:
            return {**res, "error": "FNG EMP285 permite plazo entre 12 y 120 meses."}
        if monto_smmlv < 1 or monto_smmlv > 36:
            return {**res, "error": "FNG EMP285 permite monto entre 1 y 36 SMMLV."}
        lineas_consumo_validas = ["libre inversion", "cupo rotativo", "cesion de cdat", "lineas especiales"]
        if "micro" not in tipo_credito_norm and not any(token in tipo_credito_norm for token in lineas_consumo_validas):
            return {**res, "error": "FNG EMP285 aplica para líneas de Consumo y Microcrédito."}
        tasa = FNG_EMP285_RATES.get(plazo_int)
        if tasa is None:
            return {**res, "error": f"No hay tasa FNG EMP285 para plazo {plazo_int}."}
        valor_base = monto * (tasa / 100)
        valor_iva = valor_base * IVA
        return {
            "comision_tipo": "FNG EMP285",
            "comision_tasa_base": round(tasa, 4),
            "comision_tasa_total": round(tasa * (1 + IVA), 4),
            "comision_valor": round(valor_base, 2),
            "comision_iva_valor": round(valor_iva, 2),
            "comision_total_valor": round(valor_base + valor_iva, 2),
        }
    return res


def obtener_tasa_automatica(tipo_credito, forma_pago, perfil_riesgo):
    tipo_credito_norm = normaliza(tipo_credito)
    if "lineas especiales" in tipo_credito_norm:
        return None

    categoria = categoria_riesgo(perfil_riesgo) or "NA"
    linea = tipo_credito
    forma = forma_pago
    sub_categoria = "General"
    categoria_lookup = categoria

    if "cupo rotativo" in tipo_credito_norm:
        linea = "Cupo Rotativo"
        forma = "Ventanilla"
        sub_categoria = "General"
        categoria_lookup = "NA"
    elif "cesion de cdat" in tipo_credito_norm:
        linea = "Cesion de CDAT"
        forma = "Ventanilla"
        sub_categoria = "General"
        categoria_lookup = "NA"
    elif "libre inversion" in tipo_credito_norm:
        linea = "Libre inversion"
        if "educacion" in tipo_credito_norm:
            sub_categoria = "Educacion"
            forma = "Ventanilla"
            categoria_lookup = "NA"
        elif "empresas privadas" in tipo_credito_norm:
            sub_categoria = "Empresas privadas"
            forma = "Libranza"
        elif "oficiales" in tipo_credito_norm:
            sub_categoria = "Oficiales"
            forma = "Libranza"
        elif "pensionados" in tipo_credito_norm:
            sub_categoria = "Pensionados"
            forma = "Libranza"
        else:
            sub_categoria = "General"
            forma = "Ventanilla"
            categoria_lookup = "NA"

    tasa_obj = TasaInteresConsumo.objects.filter(
        linea_credito__iexact=linea,
        forma_pago__iexact=forma,
        sub_categoria__iexact=sub_categoria,
        categoria_riesgo__iexact=categoria_lookup,
    ).first()
    if tasa_obj:
        return tasa_obj.tasa_ea

    tasa_fallback = TasaInteresConsumo.objects.filter(
        linea_credito__iexact=tipo_credito,
        forma_pago__iexact=forma_pago,
        categoria_riesgo__iexact=categoria,
    ).first()
    return tasa_fallback.tasa_ea if tasa_fallback else None


def obtener_estimacion(parametros):
    resultados = []
    for parametro, nivel in parametros.items():
        if not nivel:
            continue
        configuracion = ConfiguracionRegresion.objects.filter(parametro=parametro, nivel=nivel).first()
        if configuracion is None:
            nivel_normalizado = _normalize_lookup_value(nivel)
            configuraciones = ConfiguracionRegresion.objects.filter(parametro=parametro).only("nivel", "estimacion")
            for candidate in configuraciones:
                if _normalize_lookup_value(candidate.nivel) == nivel_normalizado:
                    configuracion = candidate
                    break
        if configuracion is None:
            continue
        resultados.append(configuracion.estimacion)
    suma_estimaciones = sum(float(item) for item in resultados if isinstance(item, Decimal))
    return INTERCEPTO + suma_estimaciones


def sumar_ingresos(asalariados, pensionados, prestadores_prof, independientes, rentistas_capital, transportadores):
    total = ((to_float(asalariados) * 0.92) + (to_float(pensionados) * 0.90) + (to_float(prestadores_prof) * 0.70) + (to_float(independientes) * 0.70) + (to_float(rentistas_capital) * 0.90) + (to_float(transportadores) * 0.90))
    return round(total, 2)


def calcular_ctas_tarjetas_credito_rotativos(cupos_tarjetas_rotativos, tasa_cupos_rotativos):
    cupostarjetas = to_float(cupos_tarjetas_rotativos)
    tasa_cupos_porcentaje = to_float(tasa_cupos_rotativos)
    tasa_cupos_efectiva_anual = tasa_cupos_porcentaje / 100
    cuotas, n, porcentaje_constante = 36, 12, 0.00098
    tasa_nominal = n * ((1 + tasa_cupos_efectiva_anual) ** (1 / n) - 1)
    tasa_mensual = (tasa_nominal / n) + porcentaje_constante
    if tasa_mensual == 0:
        return round(cupostarjetas / cuotas, 2)
    return round((tasa_mensual * cupostarjetas) / (1 - (1 + tasa_mensual) ** -cuotas), 2)


def obtener_porcentaje(asalariados, pensionados, prestadores_prof, independientes, rentistas_capital, transportadores, personas_cargo_ingresos, zona_residencia):
    suma_ingresos = (
        to_float(asalariados)
        + to_float(pensionados)
        + to_float(prestadores_prof)
        + to_float(independientes)
        + to_float(rentistas_capital)
        + to_float(transportadores)
    )
    cantidad_salarios_minimos = max(1, min(10, math.ceil(suma_ingresos / SMMLV_CONSUMO)))
    try:
        registro = ConfiguracionGastosFamiliares.objects.get(salario_minimo=cantidad_salarios_minimos, cant_personasacargo=int(personas_cargo_ingresos) if personas_cargo_ingresos else 0, zona=zona_residencia)
        return float(registro.porcentaje)
    except (ConfiguracionGastosFamiliares.DoesNotExist, ValueError, TypeError):
        return 0


def calcular_gastos_familiares(asalariados, pensionados, prestadores_prof, independientes, rentistas_capital, transportadores, porcentaje_personas_cargo):
    suma_ingresos = to_float(asalariados) + to_float(pensionados) + to_float(prestadores_prof) + to_float(independientes) + to_float(rentistas_capital) + to_float(transportadores)
    return round(suma_ingresos * (to_float(porcentaje_personas_cargo) / 100), 2)


def calcular_total_egresos(cuotas_creditos_egresos, cuotas_tarjetas_credito, cuotas_creditos_codeudor, valor_cuotas_recoge_per, valor_cuotas_recoge_nom, total_gastos_personales_fam, otros_descuentos):
    total = to_float(cuotas_creditos_egresos) + to_float(cuotas_tarjetas_credito) + to_float(cuotas_creditos_codeudor) + to_float(otros_descuentos) - (to_float(valor_cuotas_recoge_per) + to_float(valor_cuotas_recoge_nom)) + to_float(total_gastos_personales_fam)
    return round(total, 2)


def calcular_vida_deudores(monto_solicitado):
    return round(to_float(monto_solicitado) * (0.090 / 100), 2)


def calcular_valor_cuota(monto_solicitado, plazo, tasa_efectiva_anual, vida_deudores, capitalizacion_aportes):
    monto = to_float(monto_solicitado)
    plazo_cuotas = int(to_float(plazo))
    tasa_anual = to_float(tasa_efectiva_anual)
    vida = to_float(vida_deudores)
    capitalizacion = to_float(capitalizacion_aportes)
    if monto <= 0 or plazo_cuotas <= 0 or tasa_anual <= 0:
        return round(vida + capitalizacion, 2)
    tasa_mensual = tasa_anual / 12 / 100
    cuota_mensual = npf.pmt(tasa_mensual, plazo_cuotas, -monto)
    return round(cuota_mensual + vida + capitalizacion, 2)


def calcular_capacidad_pago_mensual(total_ingresos, total_egresos):
    return round(to_float(total_ingresos) - to_float(total_egresos), 2)


def calcular_restriccion_descuento_admitido(total_ingresos, otros_descuentos, valor_cuotas_recoge_nom):
    return round(((to_float(total_ingresos) / 2) + to_float(otros_descuentos)) + to_float(valor_cuotas_recoge_nom), 2)


def suma_ingresos_bruta(*valores):
    return sum(to_float(v) for v in valores)


def calcular_endeudamiento_directo(valor_cuota, cuotas_creditos_egresos, cuotas_tarjetas_credito, valor_cuotas_recoge_per, valor_cuotas_recoge_nom, *ingresos):
    suma_ingresos = suma_ingresos_bruta(*ingresos)
    if suma_ingresos == 0:
        return 0
    numerador = to_float(valor_cuota) + to_float(cuotas_creditos_egresos) + to_float(cuotas_tarjetas_credito) - (to_float(valor_cuotas_recoge_per) + to_float(valor_cuotas_recoge_nom))
    return round((numerador / suma_ingresos) * 100, 2)


def calcular_afectacion_directa(valor_cuota, cuotas_creditos_egresos, cuotas_tarjetas_credito, total_gastos_personales_fam, valor_cuotas_recoge_per, valor_cuotas_recoge_nom, *ingresos):
    suma_ingresos = suma_ingresos_bruta(*ingresos)
    if suma_ingresos == 0:
        return 0
    numerador = to_float(cuotas_creditos_egresos) + to_float(cuotas_tarjetas_credito) + to_float(total_gastos_personales_fam) + to_float(valor_cuota) - (to_float(valor_cuotas_recoge_per) + to_float(valor_cuotas_recoge_nom))
    return round((numerador / suma_ingresos) * 100, 2)


def calcular_afectacion_total(cuotas_creditos_egresos, cuotas_tarjetas_credito, total_gastos_personales_fam, cuotas_creditos_codeudor, valor_cuota, valor_cuotas_recoge_per, valor_cuotas_recoge_nom, *ingresos):
    suma_ingresos = suma_ingresos_bruta(*ingresos)
    if suma_ingresos == 0:
        return 0
    numerador = to_float(cuotas_creditos_egresos) + to_float(cuotas_tarjetas_credito) + to_float(total_gastos_personales_fam) + to_float(cuotas_creditos_codeudor) + to_float(valor_cuota) - (to_float(valor_cuotas_recoge_per) + to_float(valor_cuotas_recoge_nom))
    return round((numerador / suma_ingresos) * 100, 2)


def calcular_endeudamiento(valor_pasivos, valor_pasivos_recoge, valor_activos):
    valor_activos_num = to_float(valor_activos)
    if valor_activos_num == 0:
        return 0
    return round(((to_float(valor_pasivos) - to_float(valor_pasivos_recoge)) / valor_activos_num) * 100, 2)


def calcular_endeudamiento_credito(valor_pasivos, monto_solicitado, valor_pasivos_recoge, valor_activos, tipo_credito):
    valor_activos_num = to_float(valor_activos)
    if valor_activos_num == 0:
        return 0
    numerador = (to_float(valor_pasivos) + to_float(monto_solicitado)) - to_float(valor_pasivos_recoge)
    return round((numerador / valor_activos_num) * 100, 2)


def obtener_valor_canal(canal_agencia):
    try:
        return ConfiguracionAgenciaCanal.objects.get(canal=canal_agencia).puntos
    except ConfiguracionAgenciaCanal.DoesNotExist:
        return 0


def resolver_garantia_modelo(tipo_garantia, garantia_actual):
    garantia_norm = normaliza(tipo_garantia)
    if garantia_norm in {"fng emp319", "fng epm 319", "fng emp285", "fng epm 285", "fga", "codeudor"}:
        return "Codeudor o Fondo Garant\u00edas"
    return garantia_actual


def calcular_homologado(suma_estimaciones, valor_canal):
    exponente = math.exp(-to_float(suma_estimaciones))
    resultado = 1000 - ((1 / (1 + exponente)) * 1000)
    return round(resultado - int(to_float(valor_canal)), 2)


def calcular_weibull_acumulativo(total_homologado):
    x_homologado = int(to_float(total_homologado))
    return round(1 - weibull_min.cdf(x_homologado, 3.50, scale=500), 9)


def calcular_perfil_credito(total_homologado):
    return "Sujeto de crédito" if to_float(total_homologado) > 500 else "Fuera del apetito"


def calcular_capacidad_pago_decision(valor_cuota, restriccion_descuento, forma_pago, total_ingresos, capacidad_pago_mensual):
    if (to_float(valor_cuota) > to_float(restriccion_descuento)) and (normaliza(forma_pago) == "nomina"):
        return "Sin capacidad de descuento"
    if to_float(total_ingresos) <= 0:
        return ""
    return "Con capacidad" if to_float(capacidad_pago_mensual) >= to_float(valor_cuota) else "Sin capacidad"


def calcular_perfil_riesgo(total_homologado):
    homologado = to_float(total_homologado)
    if homologado > 875:
        return "Categoria A"
    if homologado > 750:
        return "Categoria B"
    if homologado > 625:
        return "Categoria C"
    return "Categoria D"


def calcular_decision(total_ingresos, forma_pago, perfil_credito, capacidad_pago_decision, endeudamiento_directo, afectacion_directa, afectacion_total, endeudamiento, endeudamiento_credito, monto_max_aprobado, monto_solicitado):
    if any([to_float(endeudamiento_directo) > 50, to_float(afectacion_directa) > 80, to_float(afectacion_total) > 100, to_float(endeudamiento) > 80, to_float(endeudamiento_credito) > 100]):
        return "Crédito Negado"
    if to_float(total_ingresos) == 0:
        return "Crédito Negado"
    if normaliza(forma_pago) == "nomina" and capacidad_pago_decision == "Sin capacidad de descuento":
        return "Crédito Negado"
    if capacidad_pago_decision == "Sin capacidad":
        return "Crédito Negado"
    if perfil_credito == "Fuera del apetito":
        return "Zona gris"
    return "Crédito Aprobado"


def calculos_cupo_individual_cred_patrimonio(valor_weibull_acumulativo):
    patrimonio, salario_minimo = 15900000000, 1300000
    monto_minimo_indiv_cred = salario_minimo
    percent_lim_indiv_cred_max = ((salario_minimo * 583) / patrimonio) * 100
    monto_max_inv_cred = math.ceil(patrimonio * (percent_lim_indiv_cred_max / 100))
    percent_max_expo_riesgo_cred = 36.79 / 100
    if valor_weibull_acumulativo > percent_max_expo_riesgo_cred:
        return 0
    percent_perfil_riesgo_cred_max = 1.0
    percent_perfil_riesgo_cred_min = 1 - percent_max_expo_riesgo_cred
    ln_min, ln_max = math.log(monto_minimo_indiv_cred), math.log(monto_max_inv_cred)
    value_ln_a = (ln_min - (percent_perfil_riesgo_cred_min / percent_perfil_riesgo_cred_max) * ln_max) * (percent_perfil_riesgo_cred_max / (percent_perfil_riesgo_cred_max - percent_perfil_riesgo_cred_min))
    value_b = (ln_max - value_ln_a) / percent_perfil_riesgo_cred_max
    value_a = monto_max_inv_cred / math.exp(value_b * percent_perfil_riesgo_cred_max)
    monto = value_a * math.exp(value_b * (1 - valor_weibull_acumulativo))
    return round(monto / 1000000) * 1000000


def calculos_monto_maximo_aprobacion(tasa_efectiva_anual, plazo, capacidad_pago_mensual):
    tasa = to_float(tasa_efectiva_anual)
    val_plazo = int(to_float(plazo))
    capacidad = to_float(capacidad_pago_mensual)
    if tasa == 0 or val_plazo == 0 or capacidad <= 0:
        return 0
    tasa_interes_mensual = (tasa / 100) / 12
    if tasa_interes_mensual == 0:
        return 0
    monto = capacidad * (1 - (1 + tasa_interes_mensual) ** (-val_plazo)) / tasa_interes_mensual
    return round(monto, 2)


def calcular_monto_max_aprobacion(monto1, monto2):
    return max(0, min(to_float(monto1), to_float(monto2)) - 1000000)


def decision_mostrar_monto_max(monto_max_aprobado, monto_solicitado, endeudamiento_directo, afectacion_directa, afectacion_total, endeudamiento, endeudamiento_credito, decision):
    if decision == "Crédito Negado":
        return 0
    return min(to_float(monto_max_aprobado), to_float(monto_solicitado))


def determinar_estamento(monto, linea_credito, flags):
    try:
        patrimonio_tecnico_config = ConfiguracionRegresion.objects.get(parametro="Patrimonio Tecnico", nivel="General")
        patrimonio_tecnico = float(patrimonio_tecnico_config.estimacion)
    except ObjectDoesNotExist:
        patrimonio_tecnico = 0
    monto_en_smmlv = monto / SMMLV_CONSUMO if SMMLV_CONSUMO > 0 else 0
    linea_credito_norm = normaliza(linea_credito)
    if flags.get("es_directivo") or flags.get("es_familiar_directivo") or flags.get("es_asociado_titular_5pct"):
        return "CONSEJO DE ADMINISTRACIÓN"
    if flags.get("es_trabajador_congente"):
        if any(token in linea_credito_norm for token in ["cupo rotativo", "cesion de cdat"]):
            return "COMITÉ INSTITUCIONAL DE CRÉDITO"
        return "CONSEJO DE ADMINISTRACIÓN"
    if flags.get("es_familiar_trabajador"):
        return "COMITÉ INSTITUCIONAL DE CRÉDITO"
    if any(linea in linea_credito_norm for linea in ["cupo rotativo", "libre inversion", "cesion de cdat", "lineas especiales"]):
        if 1 <= monto_en_smmlv <= 8:
            return "FÁBRICA DE CRÉDITO"
        if 8 < monto_en_smmlv <= 25:
            return "COMITÉ INTERNO DE CRÉDITO"
        if 25 < monto_en_smmlv <= 195:
            return "COMITÉ GERENCIA GENERAL"
        if 195 < monto_en_smmlv <= 583:
            return "COMITÉ INSTITUCIONAL DE CRÉDITO"
        if monto_en_smmlv > 583 and (patrimonio_tecnico == 0 or monto <= (patrimonio_tecnico * 0.10)):
            return "CONSEJO DE ADMINISTRACIÓN"
    return "No aplica"


def construir_flags_novedad(tiene_novedad, novedad_descripcion):
    flags = {"es_familiar_trabajador": False, "es_familiar_directivo": False, "es_trabajador_congente": False, "es_directivo": False, "es_asociado_titular_5pct": False}
    if str(tiene_novedad).upper() != "SI":
        return flags
    tipo_novedad_norm = normaliza(novedad_descripcion or "")
    es_un_familiar = any(keyword in tipo_novedad_norm for keyword in ["grado", "afinidad", "consanguinidad", "civil", "conyuge", "pariente"])
    cargos_directivos = ["consejo de administracion", "junta de vigilancia", "representante legal", "gerente", "directivo"]
    if es_un_familiar:
        if any(cargo in tipo_novedad_norm for cargo in cargos_directivos):
            flags["es_familiar_directivo"] = True
        elif "trabajador" in tipo_novedad_norm:
            flags["es_familiar_trabajador"] = True
    else:
        if any(cargo in tipo_novedad_norm for cargo in cargos_directivos):
            flags["es_directivo"] = True
        elif "trabajador" in tipo_novedad_norm:
            flags["es_trabajador_congente"] = True
        elif "asociado titular" in tipo_novedad_norm:
            flags["es_asociado_titular_5pct"] = True
    return flags


def evaluar_xcore_consumo(form_data, core_data, historial_data, preselecta_data):
    parametros = {
        "Estrato": form_data.get("estrato"),
        "Nivel de estudios": form_data.get("nivel_estudios"),
        "Estado Civil": form_data.get("estado_civil"),
        "Género": form_data.get("genero"),
        "Tipo de Vivienda": form_data.get("tipo_vivienda"),
        "Forma de Pago": form_data.get("forma_pago"),
        "Garantía": form_data.get("garantia"),
        "Tipo de Contrato": form_data.get("tipo_contrato"),
        "Número de Personas a Cargo": form_data.get("numero_personas_cargo"),
        "Edad": form_data.get("edad"),
        "Antiguedad del Asociado": form_data.get("antiguedad_asociado"),
        "Ingresos": form_data.get("ingresos"),
        "Score Buro de Credito Experian": form_data.get("rango_score"),
        "Aportes sociales": form_data.get("aportes_sociales"),
        "Activos": form_data.get("activos"),
        "Pasivos": form_data.get("pasivos"),
        "Ocupacion": form_data.get("ocupacion"),
    }
    suma_estimaciones = obtener_estimacion(parametros)
    total_ingresos = sumar_ingresos(form_data.get("asalariados"), form_data.get("pensionados"), form_data.get("prestadores_prof"), form_data.get("independientes"), form_data.get("rentistas_capital"), form_data.get("transportadores"))
    cuotas_tarjetas_credito = calcular_ctas_tarjetas_credito_rotativos(form_data.get("cupos_tarjetas_rotativos"), form_data.get("tasa_cupos_rotativos"))
    porcentaje_personas_cargo = obtener_porcentaje(form_data.get("asalariados"), form_data.get("pensionados"), form_data.get("prestadores_prof"), form_data.get("independientes"), form_data.get("rentistas_capital"), form_data.get("transportadores"), form_data.get("personas_cargo_ingresos"), form_data.get("zona"))
    total_gastos_personales_fam = calcular_gastos_familiares(form_data.get("asalariados"), form_data.get("pensionados"), form_data.get("prestadores_prof"), form_data.get("independientes"), form_data.get("rentistas_capital"), form_data.get("transportadores"), porcentaje_personas_cargo)
    total_egresos = calcular_total_egresos(form_data.get("cuotas_creditos_egresos"), cuotas_tarjetas_credito, form_data.get("cuotas_creditos_codeudor"), form_data.get("valor_cuotas_recoge_per"), form_data.get("valor_cuotas_recoge_nom"), total_gastos_personales_fam, form_data.get("otros_descuentos"))
    vida_deudores = calcular_vida_deudores(form_data.get("monto_solicitado"))
    capacidad_pago_mensual = calcular_capacidad_pago_mensual(total_ingresos, total_egresos)
    valor_canal = obtener_valor_canal(form_data.get("canal"))
    total_homologado = calcular_homologado(suma_estimaciones, valor_canal)
    perfil_riesgo = calcular_perfil_riesgo(total_homologado)
    comision_data = calcular_comision_garantia(form_data.get("tipo_garantia"), form_data.get("tipo_cliente"), form_data.get("forma_pago"), perfil_riesgo, form_data.get("monto_solicitado"), form_data.get("plazo"), form_data.get("tipo_credito"))
    tasa_automatica = obtener_tasa_automatica(form_data.get("tipo_credito"), form_data.get("forma_pago"), perfil_riesgo)
    tasa_efectiva_anual_final = tasa_automatica if tasa_automatica is not None else to_float(form_data.get("tasa_efectiva_anual"))
    valor_cuota = calcular_valor_cuota(form_data.get("monto_solicitado"), form_data.get("plazo"), tasa_efectiva_anual_final, vida_deudores, form_data.get("capitalizacion_aportes"))
    restriccion_descuento = calcular_restriccion_descuento_admitido(total_ingresos, form_data.get("otros_descuentos"), form_data.get("valor_cuotas_recoge_nom"))
    ingresos = (form_data.get("asalariados"), form_data.get("pensionados"), form_data.get("prestadores_prof"), form_data.get("independientes"), form_data.get("rentistas_capital"), form_data.get("transportadores"))
    endeudamiento_directo = calcular_endeudamiento_directo(valor_cuota, form_data.get("cuotas_creditos_egresos"), cuotas_tarjetas_credito, form_data.get("valor_cuotas_recoge_per"), form_data.get("valor_cuotas_recoge_nom"), *ingresos)
    afectacion_directa = calcular_afectacion_directa(valor_cuota, form_data.get("cuotas_creditos_egresos"), cuotas_tarjetas_credito, total_gastos_personales_fam, form_data.get("valor_cuotas_recoge_per"), form_data.get("valor_cuotas_recoge_nom"), *ingresos)
    afectacion_total = calcular_afectacion_total(form_data.get("cuotas_creditos_egresos"), cuotas_tarjetas_credito, total_gastos_personales_fam, form_data.get("cuotas_creditos_codeudor"), valor_cuota, form_data.get("valor_cuotas_recoge_per"), form_data.get("valor_cuotas_recoge_nom"), *ingresos)
    endeudamiento = calcular_endeudamiento(form_data.get("valor_pasivos"), form_data.get("valor_pasivos_recoge"), form_data.get("valor_activos"))
    endeudamiento_credito = calcular_endeudamiento_credito(form_data.get("valor_pasivos"), form_data.get("monto_solicitado"), form_data.get("valor_pasivos_recoge"), form_data.get("valor_activos"), form_data.get("tipo_credito"))
    perfil_credito = calcular_perfil_credito(total_homologado)
    capacidad_pago_decision = calcular_capacidad_pago_decision(valor_cuota, restriccion_descuento, form_data.get("forma_pago"), total_ingresos, capacidad_pago_mensual)
    monto1 = calculos_cupo_individual_cred_patrimonio(calcular_weibull_acumulativo(total_homologado))
    monto2 = calculos_monto_maximo_aprobacion(tasa_efectiva_anual_final, form_data.get("plazo"), capacidad_pago_mensual)
    monto_max_aprobado = calcular_monto_max_aprobacion(monto1, monto2)
    decision = calcular_decision(total_ingresos, form_data.get("forma_pago"), perfil_credito, capacidad_pago_decision, endeudamiento_directo, afectacion_directa, afectacion_total, endeudamiento, endeudamiento_credito, monto_max_aprobado, form_data.get("monto_solicitado"))
    valor_monto_decision = decision_mostrar_monto_max(monto_max_aprobado, form_data.get("monto_solicitado"), endeudamiento_directo, afectacion_directa, afectacion_total, endeudamiento, endeudamiento_credito, decision)
    flags = construir_flags_novedad(historial_data.get("tiene_novedad", "NO"), historial_data.get("novedad_descripcion", ""))
    estamento = "No aplica" if decision == "Crédito Negado" else determinar_estamento(to_float(form_data.get("monto_solicitado")), form_data.get("tipo_credito"), flags)
    return {
        "suma_estimaciones": suma_estimaciones,
        "suma_ingresos": total_ingresos,
        "cuotas_tarjetas_credito_rotativos": cuotas_tarjetas_credito,
        "gastos_personales_familiares": total_gastos_personales_fam,
        "total_valor_egresos": total_egresos,
        "total_vida_deudores": vida_deudores,
        "total_valor_cuota": valor_cuota,
        "total_capacidad_pago_mensual": capacidad_pago_mensual,
        "restriccion_descuento": restriccion_descuento,
        "total_endeudamiento_directo": endeudamiento_directo,
        "total_afectacion_directa": afectacion_directa,
        "total_afectacion_total": afectacion_total,
        "valor_endeudamiento": endeudamiento,
        "valor_endeudamiento_credito": endeudamiento_credito,
        "resultado_perfil_credito": perfil_credito,
        "resultado_capacidad_pago_decision": capacidad_pago_decision,
        "resultado_perfil_riesgo": perfil_riesgo,
        "puntaje_xcore": total_homologado,
        "decision_final": decision,
        "valor_monto_max_decision": valor_monto_decision,
        "tasa_efectiva_automatica": tasa_automatica,
        "tasa_efectiva_calculada": tasa_efectiva_anual_final,
        "estamento": estamento,
        "tiene_novedad": historial_data.get("tiene_novedad", "NO"),
        "novedad_descripcion": historial_data.get("novedad_descripcion", ""),
        **comision_data,
    }


def _format_pdf_money(value):
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.0f}".replace(",", ".")


def _format_pdf_percent(value):
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.2f}".replace(".", ",") + " %"
    except (TypeError, ValueError):
        return str(value)


def _pdf_text(value):
    if value in (None, ""):
        return ""
    return str(value)


def _draw_pdf_line(can, x, y, value, *, max_chars=None):
    text = _pdf_text(value)
    if not text:
        return
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    can.drawString(x, y, text)


def _pdf_template_path():
    return Path(__file__).resolve().parents[1] / "assets" / "Plantilla_capacidad_pago_actualizado.pdf"


def _tipo_cliente_label(value):
    normalized = normaliza(value)
    if normalized in {"n", "nuevo"}:
        return "Nuevo"
    if normalized in {"a", "antiguo"}:
        return "Antiguo"
    return _pdf_text(value)


def _build_pdf_context(solicitud, evaluacion):
    detail = getattr(solicitud, "consumo_detail", None)
    form_data = detail.form_data if detail else {}
    core_data = detail.core_data if detail else {}
    resultados = evaluacion.resultados or {}

    nombre = (
        form_data.get("nombre")
        or core_data.get("nombre")
        or solicitud.solicitante.primer_apellido
        or ""
    )

    return {
        "numero_solicitud": solicitud.numero_solicitud,
        "numero_identificacion": solicitud.solicitante.numero_identificacion,
        "nombre": nombre,
        "tipo_cliente": _tipo_cliente_label(form_data.get("tipo_cliente")),
        "estrato": form_data.get("estrato", ""),
        "estado_civil": form_data.get("estado_civil", ""),
        "tipo_vivienda": form_data.get("tipo_vivienda", ""),
        "garantia": resolver_garantia_modelo(form_data.get("tipo_garantia"), form_data.get("garantia", "")),
        "numero_personas_cargo": form_data.get("numero_personas_cargo", ""),
        "antiguedad_asociado": form_data.get("antiguedad_asociado", ""),
        "rango_score": form_data.get("rango_score", ""),
        "aportes_sociales": form_data.get("aportes_sociales", ""),
        "valor_activos": form_data.get("valor_activos", core_data.get("valor_activos", "")),
        "saldo_total_creditos": form_data.get("saldo_creditos", ""),
        "tasa_vig_cupos_rota": form_data.get("tasa_cupos_rotativos", ""),
        "canal": form_data.get("canal", ""),
        "pasivos": form_data.get("pasivos", ""),
        "tipo_garantia": form_data.get("tipo_garantia", ""),
        "nivel_estudios": form_data.get("nivel_estudios", ""),
        "genero": form_data.get("genero", ""),
        "forma_pago": form_data.get("forma_pago", ""),
        "tipo_contrato": form_data.get("tipo_contrato", ""),
        "edad": form_data.get("edad", ""),
        "ingresos": form_data.get("ingresos", ""),
        "score_data": form_data.get("valor_score", ""),
        "activos": form_data.get("activos", core_data.get("activos", "")),
        "valor_pasivos": form_data.get("valor_pasivos", ""),
        "valor_pasivos_recoge": form_data.get("valor_pasivos_recoge", ""),
        "cupos_tarjetas_rota": form_data.get("cupos_tarjetas_rotativos", ""),
        "ocupacion": form_data.get("ocupacion", ""),
        "zona": form_data.get("zona", ""),
        "tipo_credito": form_data.get("tipo_credito", ""),
        "asalariados_ing": form_data.get("asalariados", ""),
        "rentistas_ing": form_data.get("rentistas_capital", ""),
        "num_personas_cargo_ing": form_data.get("personas_cargo_ingresos", ""),
        "pensionados_ing": form_data.get("pensionados", ""),
        "independientes_ing": form_data.get("independientes", ""),
        "transportadores_ing": form_data.get("transportadores", ""),
        "prestadores_serv_ing": form_data.get("prestadores_prof", ""),
        "total_ingresos_ing": resultados.get("suma_ingresos", 0),
        "total_cuotas_cred_egr": form_data.get("cuotas_creditos_egresos", ""),
        "cuotas_tarj_cred_rot_egr": resultados.get("cuotas_tarjetas_credito_rotativos", 0),
        "cuotas_cred_codeudor_egr": form_data.get("cuotas_creditos_codeudor", ""),
        "val_cuotas_recoge_pers_egr": form_data.get("valor_cuotas_recoge_per", ""),
        "val_cuotas_recoge_nom_egr": form_data.get("valor_cuotas_recoge_nom", ""),
        "gastos_pers_fam_egr": resultados.get("gastos_personales_familiares", 0),
        "otros_descuentos_egr": form_data.get("otros_descuentos", ""),
        "total_egresos_egr": resultados.get("total_valor_egresos", 0),
        "capacidad_pago_mens_calc": resultados.get("total_capacidad_pago_mensual", 0),
        "restriccion_desc_admitido": resultados.get("restriccion_descuento", 0),
        "endeudamiento_directo": resultados.get("total_endeudamiento_directo", 0),
        "endeudamiento": resultados.get("valor_endeudamiento", 0),
        "afectacion_directa": resultados.get("total_afectacion_directa", 0),
        "afectacion_total": resultados.get("total_afectacion_total", 0),
        "endeudamiento_con_credito": resultados.get("valor_endeudamiento_credito", 0),
        "monto_solicitado_solic": form_data.get("monto_solicitado", solicitud.monto_solicitado),
        "tasa_efectiva_solic": resultados.get("tasa_efectiva_calculada", 0),
        "capitalizacion_aportes_solic": form_data.get("capitalizacion_aportes", ""),
        "plazo_solic": form_data.get("plazo", solicitud.plazo_solicitado),
        "vida_deudores_solic": evaluacion.vida_deudores or resultados.get("total_vida_deudores", 0),
        "valor_cuota_solic": evaluacion.valor_cuota or resultados.get("total_valor_cuota", 0),
        "comision_tasa_base": resultados.get("comision_tasa_base", 0),
        "comision_valor": resultados.get("comision_valor", 0),
        "comision_iva_valor": resultados.get("comision_iva_valor", 0),
        "comision_total_valor": resultados.get("comision_total_valor", 0),
        "comision_tipo_garantia": resultados.get("comision_tipo", ""),
        "perfil_credito": evaluacion.perfil_credito,
        "decision_final": evaluacion.decision_final,
        "tiene_novedad": evaluacion.tiene_novedad,
        "estamento": evaluacion.estamento,
        "capacidad_pago_final": evaluacion.capacidad_pago_final,
        "perfil_riesgo": evaluacion.perfil_riesgo,
        "novedad_descripcion": evaluacion.novedad_descripcion,
        "monto_max_posible": 0 if "negado" in normaliza(evaluacion.decision_final) else evaluacion.monto_max_posible,
        "puntaje_xcore": evaluacion.puntaje_xcore,
    }


def _draw_template_pdf(solicitud, evaluacion):
    template_path = _pdf_template_path()
    if PdfReader is None or PdfWriter is None:
        raise ValueError("No fue posible inicializar el motor de plantilla PDF: falta la dependencia pypdf.")
    if not template_path.exists():
        raise ValueError(f"No se encontró la plantilla PDF aprobada en {template_path}.")

    context = _build_pdf_context(solicitud, evaluacion)
    template_reader = PdfReader(str(template_path))
    writer = PdfWriter()

    packet = BytesIO()
    can = canvas.Canvas(packet, pagesize=letter)

    can.setFont("Helvetica-Bold", 13)
    can.setFillColor(HexColor("#ff8000"))
    can.drawString(430, 697, _pdf_text(context["numero_solicitud"]))

    can.setFont("Helvetica", 7)
    can.setFillColor(HexColor("#0A0A0A"))

    can.drawString(160, 661, _pdf_text(context["numero_identificacion"]))
    can.drawString(160, 649, _pdf_text(context["estrato"]))
    can.drawString(160, 637, _pdf_text(context["estado_civil"]))
    can.drawString(160, 626, _pdf_text(context["tipo_vivienda"]))
    can.drawString(160, 614, _pdf_text(context["garantia"]))
    can.drawString(160, 603, _pdf_text(context["numero_personas_cargo"]))
    can.drawString(160, 591, _pdf_text(context["antiguedad_asociado"]))
    can.drawString(160, 579, _pdf_text(context["rango_score"]))
    can.drawString(160, 568, _pdf_text(context["aportes_sociales"]))
    can.drawString(160, 556, _format_pdf_money(context["valor_activos"]))
    can.drawString(160, 544, _format_pdf_money(context["saldo_total_creditos"]))
    can.drawString(160, 533, _format_pdf_percent(context["tasa_vig_cupos_rota"]))
    can.drawString(160, 522, _pdf_text(context["canal"]))
    can.drawString(222, 510, _pdf_text(context["pasivos"]))
    can.drawString(160, 499, _pdf_text(context["tipo_garantia"]))

    can.drawString(410, 672, _pdf_text(context["nombre"]))
    can.drawString(410, 661, _pdf_text(context["tipo_cliente"]))
    _draw_pdf_line(can, 410, 649, context["nivel_estudios"], max_chars=34)
    can.drawString(410, 637, _pdf_text(context["genero"]))
    can.drawString(410, 626, _pdf_text(context["forma_pago"]))
    _draw_pdf_line(can, 410, 614, context["tipo_contrato"], max_chars=26)
    can.drawString(410, 603, _pdf_text(context["edad"]))
    _draw_pdf_line(can, 410, 591, context["ingresos"], max_chars=28)
    can.drawString(410, 579, _format_pdf_money(context["score_data"]))
    can.drawString(410, 568, _pdf_text(context["activos"]))
    can.drawString(410, 556, _format_pdf_money(context["valor_pasivos"]))
    can.drawString(470, 544, _format_pdf_money(context["valor_pasivos_recoge"]))
    can.drawString(470, 533, _format_pdf_money(context["cupos_tarjetas_rota"]))
    can.drawString(410, 522, _pdf_text(context["ocupacion"]))
    can.drawString(410, 510, _pdf_text(context["zona"]))
    _draw_pdf_line(can, 410, 499, context["tipo_credito"], max_chars=24)

    can.drawString(160, 475, _format_pdf_money(context["asalariados_ing"]))
    can.drawString(160, 464, _format_pdf_money(context["rentistas_ing"]))
    can.drawString(160, 452, _pdf_text(context["num_personas_cargo_ing"]))

    can.drawString(410, 475, _format_pdf_money(context["pensionados_ing"]))
    can.drawString(410, 464, _format_pdf_money(context["independientes_ing"]))
    can.drawString(410, 452, _format_pdf_money(context["transportadores_ing"]))
    can.drawString(410, 441, _format_pdf_money(context["prestadores_serv_ing"]))
    can.drawString(410, 429, _format_pdf_money(context["total_ingresos_ing"]))

    can.drawString(410, 406, _format_pdf_money(context["total_cuotas_cred_egr"]))
    can.drawString(410, 394, _format_pdf_money(context["cuotas_tarj_cred_rot_egr"]))
    can.drawString(410, 382, _format_pdf_money(context["cuotas_cred_codeudor_egr"]))
    can.drawString(410, 371, _format_pdf_money(context["val_cuotas_recoge_pers_egr"]))
    can.drawString(410, 359, _format_pdf_money(context["val_cuotas_recoge_nom_egr"]))
    can.drawString(410, 348, _format_pdf_money(context["gastos_pers_fam_egr"]))
    can.drawString(160, 336, _format_pdf_money(context["otros_descuentos_egr"]))
    can.drawString(410, 336, _format_pdf_money(context["total_egresos_egr"]))
    can.drawString(410, 317, _format_pdf_money(context["capacidad_pago_mens_calc"]))
    can.drawString(410, 306, _format_pdf_money(context["restriccion_desc_admitido"]))
    can.drawString(125, 286, _format_pdf_percent(context["endeudamiento_directo"]))
    can.drawString(125, 274, _format_pdf_percent(context["endeudamiento"]))
    can.drawString(270, 286, _format_pdf_percent(context["afectacion_directa"]))
    can.drawString(270, 274, _format_pdf_percent(context["afectacion_total"]))
    can.drawString(465, 286, _format_pdf_percent(context["endeudamiento_con_credito"]))

    can.drawString(160, 251, _format_pdf_money(context["monto_solicitado_solic"]))
    can.drawString(160, 240, _format_pdf_percent(context["tasa_efectiva_solic"]))
    can.drawString(160, 229, _format_pdf_money(context["capitalizacion_aportes_solic"]))
    can.drawString(410, 251, _pdf_text(context["plazo_solic"]))
    can.drawString(410, 240, _format_pdf_money(context["vida_deudores_solic"]))
    can.drawString(410, 229, _format_pdf_money(context["valor_cuota_solic"]))

    aplica_comision = bool(_pdf_text(context["comision_tipo_garantia"]).strip())
    can.drawString(160, 206, _format_pdf_percent(context["comision_tasa_base"]) if aplica_comision else "No aplica")
    can.drawString(410, 206, _format_pdf_money(context["comision_valor"]) if aplica_comision else "No aplica")
    can.drawString(160, 195, _format_pdf_money(context["comision_iva_valor"]) if aplica_comision else "No aplica")
    can.drawString(410, 195, _format_pdf_money(context["comision_total_valor"]) if aplica_comision else "No aplica")

    can.drawString(160, 172, _pdf_text(context["perfil_credito"]))
    can.drawString(160, 160, _pdf_text(context["decision_final"]))
    can.drawString(108, 148, "SI" if context["tiene_novedad"] else "NO")
    can.drawString(160, 136, _pdf_text(context["estamento"]))
    can.drawString(410, 172, _pdf_text(context["capacidad_pago_final"]))
    can.drawString(410, 160, _pdf_text(context["perfil_riesgo"]))
    can.drawString(290, 148, _pdf_text(context["novedad_descripcion"]))
    can.drawString(465, 136, _format_pdf_money(context["monto_max_posible"]))

    can.save()
    packet.seek(0)
    overlay_pdf = PdfReader(packet)

    for index, page in enumerate(template_reader.pages):
        if index == 0:
            page.merge_page(overlay_pdf.pages[0])
        writer.add_page(page)

    output_buffer = BytesIO()
    writer.write(output_buffer)
    return output_buffer.getvalue()


def build_consumo_decision_pdf(solicitud, evaluacion):
    return _draw_template_pdf(solicitud, evaluacion)


def encode_pdf_base64(pdf_bytes: bytes) -> str:
    return base64.b64encode(pdf_bytes).decode("utf-8")


