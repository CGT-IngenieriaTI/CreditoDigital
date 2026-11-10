"""Reglas deterministicas; no consulta fuentes ni calcula capacidad/riesgo."""

from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Callable

from .contexto import Certificacion, ContextoEvaluacionCrediHoy, ObligacionInterna
from .parametros import PoliticaCrediHoy
from .resultados import EstadoRegla, ResultadoRegla


VERSION_REGLAS = "1"


def _numero(valor):
    if type(valor) is int:
        return Decimal(valor)
    if isinstance(valor, Decimal) and valor.is_finite():
        return valor
    return None


def _multiplicar(uno, dos):
    # No depender del redondeo/precision Decimal del proceso que invoca el motor.
    precision = max(28, len(uno.as_tuple().digits) + len(dos.as_tuple().digits))
    with localcontext(Context(prec=precision)):
        return uno * dos


def _texto(valor):
    return isinstance(valor, str) and bool(valor.strip())


def _presente(hecho):
    return hecho.disponible and hecho.valor is not None


def _certificado(hecho):
    return _presente(hecho) and hecho.estado == Certificacion.CERTIFICADO


def _utilizable(hecho, contexto, *, certificado=False, corte=False):
    if not _presente(hecho) or not _texto(hecho.fuente) or hecho.fuente == "NO_INFORMADA":
        return False
    if certificado and not _certificado(hecho):
        return False
    if corte and hecho.fecha_corte is None:
        return False
    if hecho.fecha_corte is not None and (
        type(hecho.fecha_corte) is not date or hecho.fecha_corte > contexto.fecha_evaluacion
    ):
        return False
    return hecho.periodo is None or _periodo_valido(hecho.periodo)


def _fuentes(*hechos):
    return tuple(dict.fromkeys(h.fuente for h in hechos))


def _resultado(codigo, estado, motivo, valor, parametro, fuentes):
    cumple = {EstadoRegla.CUMPLE: True, EstadoRegla.NO_CUMPLE: False}.get(estado)
    return ResultadoRegla(
        codigo=codigo, cumple=cumple, estado=estado, motivo=motivo,
        valor_evaluado=valor, parametro=parametro, fuente=fuentes,
        version_regla=VERSION_REGLAS,
    )


def _producto(c, p):
    estado = EstadoRegla.INFORMACION_INSUFICIENTE
    motivo = "PRODUCTO_NO_DISPONIBLE"
    if _texto(c.producto):
        estado = EstadoRegla.CUMPLE if c.producto == p.producto_permitido else EstadoRegla.NO_CUMPLE
        motivo = "PRODUCTO_ADMITIDO" if estado == EstadoRegla.CUMPLE else "PRODUCTO_NO_ADMITIDO"
    return _resultado("CH01_PRODUCTO", estado, motivo, {"producto": c.producto},
                      {"producto_permitido": p.producto_permitido}, ("FORMULARIO",))


def _plazo(c, p):
    estado, motivo = EstadoRegla.INFORMACION_INSUFICIENTE, "PLAZO_NO_DISPONIBLE_O_INVALIDO"
    if type(c.plazo) is int:
        cumple = p.plazo_minimo <= c.plazo <= p.plazo_maximo
        estado = EstadoRegla.CUMPLE if cumple else EstadoRegla.NO_CUMPLE
        motivo = "PLAZO_ADMITIDO" if cumple else "PLAZO_FUERA_RANGO"
    return _resultado("CH02_PLAZO", estado, motivo, {"plazo": c.plazo},
                      {"minimo": p.plazo_minimo, "maximo": p.plazo_maximo}, ("FORMULARIO",))


def _smmlv(c):
    valor = _numero(c.smmlv.valor) if _utilizable(c.smmlv, c) else None
    return valor if valor is not None and valor > 0 else None


def _monto_minimo(c, p):
    monto, smmlv = _numero(c.monto), _smmlv(c)
    minimo = _multiplicar(smmlv, p.porcentaje_monto_minimo_smmlv) if smmlv is not None else None
    estado, motivo = EstadoRegla.INFORMACION_INSUFICIENTE, "MONTO_O_SMMLV_NO_DISPONIBLE"
    if monto is not None and minimo is not None:
        cumple = monto >= minimo
        estado = EstadoRegla.CUMPLE if cumple else EstadoRegla.NO_CUMPLE
        motivo = "MONTO_MINIMO_CUMPLIDO" if cumple else "MONTO_INFERIOR_MINIMO"
    return _resultado("CH03_MONTO_MINIMO", estado, motivo, {"monto": monto, "smmlv": smmlv},
                      {"porcentaje": p.porcentaje_monto_minimo_smmlv, "minimo": minimo},
                      ("FORMULARIO", *_fuentes(c.smmlv)))


def _aporte(c):
    hecho = c.interno.aporte_social_203
    valor = _numero(hecho.valor) if _utilizable(hecho, c, certificado=True, corte=True) else None
    return valor if valor is not None and valor >= 0 else None


def _tope_monto(c, p):
    monto, smmlv, aporte = _numero(c.monto), _smmlv(c), _aporte(c)
    general = _multiplicar(smmlv, p.maximo_smmlv) if smmlv is not None else None
    por_aportes = _multiplicar(aporte, p.porcentaje_maximo_aportes) if aporte is not None else None
    topes = [v for v in (general, por_aportes) if v is not None]
    estado, motivo = EstadoRegla.INFORMACION_INSUFICIENTE, "TOPE_NO_EVALUABLE"
    # Un tope conocido basta para probar un incumplimiento independiente.
    if monto is not None and any(monto > tope for tope in topes):
        estado, motivo = EstadoRegla.NO_CUMPLE, "MONTO_SUPERA_TOPE"
    elif monto is not None and len(topes) == 2:
        estado, motivo = EstadoRegla.CUMPLE, "MONTO_DENTRO_TOPES"
    return _resultado("CH04_TOPE_MONTO", estado, motivo,
                      {"monto": monto, "smmlv": smmlv, "aporte_social_203": aporte},
                      {"maximo_smmlv": p.maximo_smmlv, "porcentaje_aportes": p.porcentaje_maximo_aportes,
                       "tope_general": general, "tope_aportes": por_aportes,
                       "tope_aplicable": min(topes) if len(topes) == 2 else None},
                      ("FORMULARIO", *_fuentes(c.smmlv, c.interno.aporte_social_203)))


def _garantia_pago(c, p):
    invalidos, faltantes = [], []
    for nombre, valor, permitidos in (
        ("garantia", c.garantia, p.garantias_permitidas),
        ("forma_pago", c.forma_pago, p.formas_pago_permitidas),
    ):
        if not _texto(valor):
            faltantes.append(nombre)
        elif valor not in permitidos:
            invalidos.append(nombre)
    estado, motivo = EstadoRegla.CUMPLE, "GARANTIA_Y_PAGO_ADMITIDOS"
    if invalidos:
        estado, motivo = EstadoRegla.NO_CUMPLE, "MODALIDAD_NO_ADMITIDA"
    elif faltantes:
        estado, motivo = EstadoRegla.INFORMACION_INSUFICIENTE, "MODALIDAD_NO_DISPONIBLE"
    return _resultado("CH05_GARANTIA_FORMA_PAGO", estado, motivo,
                      {"garantia": c.garantia, "forma_pago": c.forma_pago,
                       "invalidos": tuple(invalidos), "faltantes": tuple(faltantes)},
                      {"garantias": p.garantias_permitidas, "formas_pago": p.formas_pago_permitidas},
                      ("FORMULARIO",))


def _excepcion_mora(c, p):
    pendiente = c.solicita_excepcion_mora
    return _resultado("CH06_EXCEPCION_MORA",
                      EstadoRegla.PENDIENTE_POLITICA if pendiente else EstadoRegla.NO_APLICA,
                      "EXCEPCION_MORA_PENDIENTE_POLITICA" if pendiente else "EXCEPCION_NO_SOLICITADA",
                      {"solicita_excepcion_mora": pendiente},
                      {"semantica": "PENDIENTE_POLITICA"}, ("FORMULARIO", "POLITICA"))


def _cuota_interna(c, p):
    obligaciones, cuota = c.interno.obligaciones_internas, c.interno.cuota_vigente_interna
    periodo = c.interno.periodo_anterior
    conocidas = (
        _utilizable(obligaciones, c, certificado=True)
        and _obligaciones_validas(obligaciones.valor)
        and _utilizable(periodo, c, certificado=True)
        and _periodo_valido(periodo.valor)
        and periodo.valor < c.fecha_evaluacion.strftime("%Y-%m")
        and obligaciones.periodo == periodo.valor
        and periodo.periodo in (None, periodo.valor)
    )
    cantidad = len(obligaciones.valor) if conocidas else None
    valor_cuota = _numero(cuota.valor) if _utilizable(cuota, c, certificado=True, corte=True) else None
    disponible = valor_cuota is not None and valor_cuota >= 0
    estado, motivo = EstadoRegla.INFORMACION_INSUFICIENTE, "OBLIGACIONES_INTERNAS_NO_DISPONIBLES"
    if cantidad == 0:
        estado, motivo = EstadoRegla.NO_APLICA, "SIN_OBLIGACIONES_INTERNAS"
    elif cantidad is not None:
        estado = EstadoRegla.CUMPLE if disponible else EstadoRegla.REVISION_MANUAL
        motivo = "CUOTA_INTERNA_DISPONIBLE" if disponible else "REVISION_MANUAL_CAPACIDAD"
    return _resultado("CH07_CUOTA_INTERNA", estado, motivo,
                      {"cantidad_obligaciones": cantidad, "cuotaInternaDisponible": disponible,
                       "cuota_vigente_interna": valor_cuota},
                      {"requiere_cuota_certificada_si_hay_obligaciones": True},
                      _fuentes(obligaciones, cuota))


def _periodo_valido(valor):
    if not isinstance(valor, str) or len(valor) != 7:
        return False
    try:
        return date.fromisoformat(valor + "-01").strftime("%Y-%m") == valor
    except ValueError:
        return False


def _no_negativo(valor):
    numero = _numero(valor)
    return numero is not None and numero >= 0


def _entero_no_negativo(valor):
    return type(valor) is int and valor >= 0


def _obligaciones_validas(valor):
    if not isinstance(valor, tuple):
        return False
    claves = set()
    for obligacion in valor:
        if not isinstance(obligacion, ObligacionInterna):
            return False
        if not all(_texto(v) for v in (obligacion.a_tipodr, obligacion.a_obliga, obligacion.q_clasif)):
            return False
        if not all(_entero_no_negativo(v) for v in (obligacion.q_diamora, obligacion.q_diaven)):
            return False
        if not all(_no_negativo(v) for v in (obligacion.v_salmor, obligacion.v_salkap)):
            return False
        clave = (obligacion.a_tipodr, obligacion.a_obliga)
        if clave in claves:
            return False
        claves.add(clave)
    return True


def _calidad(c, p):
    problemas, fuentes = [], []

    def revisar(nombre, hecho, valida, *, certificado=False, corte=False):
        fuentes.append(hecho.fuente)
        if not _presente(hecho) or not valida(hecho.valor):
            problemas.append(nombre)
        if not _texto(hecho.fuente) or hecho.fuente == "NO_INFORMADA":
            problemas.append(nombre + ".fuente")
        if certificado and hecho.estado != Certificacion.CERTIFICADO:
            problemas.append(nombre + ".certificacion")
        if corte and hecho.fecha_corte is None:
            problemas.append(nombre + ".fecha_corte")
        if hecho.fecha_corte is not None and (
            type(hecho.fecha_corte) is not date or hecho.fecha_corte > c.fecha_evaluacion
        ):
            problemas.append(nombre + ".fecha_corte")
        if hecho.periodo is not None and not _periodo_valido(hecho.periodo):
            problemas.append(nombre + ".periodo")

    if not p.vigente(c.fecha_evaluacion):
        problemas.append("politica.vigencia")
    for nombre in ("solicitud", "solicitante"):
        if not _texto(getattr(c, nombre)):
            problemas.append(nombre)
    revisar("ingresos", c.ingresos, _no_negativo)
    revisar("smmlv", c.smmlv, lambda v: _numero(v) is not None and _numero(v) > 0)
    if c.datacredito.disponible is not True:
        problemas.append("datacredito.disponible")
    validadores = (
        ("obligaciones", lambda v: isinstance(v, tuple) and all(_texto(x) for x in v)),
        ("mora", _entero_no_negativo), ("score", lambda v: _numero(v) is not None),
        ("categoria", _texto), ("embargo", lambda v: type(v) is bool),
        ("experiencia", _entero_no_negativo),
    )
    for nombre, valida in validadores:
        revisar("datacredito." + nombre, getattr(c.datacredito, nombre), valida, corte=True)
    interno = c.interno
    revisar("interno.estado_asociado", interno.estado_asociado, _texto)
    revisar("interno.periodo_anterior", interno.periodo_anterior, _periodo_valido, certificado=True)
    revisar("interno.obligaciones_internas", interno.obligaciones_internas,
            _obligaciones_validas, certificado=True)
    periodo = interno.periodo_anterior.valor
    if _periodo_valido(periodo):
        if periodo >= c.fecha_evaluacion.strftime("%Y-%m"):
            problemas.append("interno.periodo_anterior.no_anterior")
        if interno.obligaciones_internas.periodo != periodo:
            problemas.append("interno.obligaciones_internas.periodo_no_coincide")
        if interno.periodo_anterior.periodo not in (None, periodo):
            problemas.append("interno.periodo_anterior.periodo_no_coincide")
    revisar("interno.aporte_social_203", interno.aporte_social_203,
            _no_negativo, certificado=True, corte=True)
    # C3 se resuelve en CH07: su ausencia no es un rechazo ni una cuota cero.
    return _resultado("CH08_CALIDAD_EVIDENCIA",
                      EstadoRegla.INFORMACION_INSUFICIENTE if problemas else EstadoRegla.CUMPLE,
                      "EVIDENCIA_INCOMPLETA_O_INVALIDA" if problemas else "EVIDENCIA_ESTRUCTURAL_COMPLETA",
                      {"faltantes_o_invalidos": tuple(dict.fromkeys(problemas))},
                      {"contrato": "CONTEXTO_NORMALIZADO_D1_D2", "valida_riesgo_o_capacidad": False},
                      tuple(dict.fromkeys(fuentes)))


@dataclass(frozen=True)
class ReglaCrediHoy:
    codigo: str
    descripcion: str
    evaluador: Callable[[ContextoEvaluacionCrediHoy, PoliticaCrediHoy], ResultadoRegla]

    def evaluar(self, contexto, politica):
        if self.codigo != "CH08_CALIDAD_EVIDENCIA" and not politica.vigente(contexto.fecha_evaluacion):
            return _resultado(self.codigo, EstadoRegla.INFORMACION_INSUFICIENTE,
                              "POLITICA_NO_VIGENTE", {},
                              {"desde": politica.vigencia_desde, "hasta": politica.vigencia_hasta},
                              ("POLITICA",))
        return self.evaluador(contexto, politica)


REGLAS_CREDIHOY = (
    ReglaCrediHoy("CH01_PRODUCTO", "Producto admitido", _producto),
    ReglaCrediHoy("CH02_PLAZO", "Plazo inclusivo", _plazo),
    ReglaCrediHoy("CH03_MONTO_MINIMO", "Monto minimo en SMMLV", _monto_minimo),
    ReglaCrediHoy("CH04_TOPE_MONTO", "Topes general y por aportes 203", _tope_monto),
    ReglaCrediHoy("CH05_GARANTIA_FORMA_PAGO", "Garantia y forma de pago", _garantia_pago),
    ReglaCrediHoy("CH06_EXCEPCION_MORA", "Excepcion pendiente de politica", _excepcion_mora),
    ReglaCrediHoy("CH07_CUOTA_INTERNA", "Disponibilidad de C3", _cuota_interna),
    ReglaCrediHoy("CH08_CALIDAD_EVIDENCIA", "Completitud estructural de hechos", _calidad),
)
