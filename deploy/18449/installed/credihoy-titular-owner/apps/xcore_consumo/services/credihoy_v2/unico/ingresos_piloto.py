"""Alcance salarial del piloto, separado de ingresos reales y politica autorizada."""

from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re

from ..base import EstadoCertificacion as EC, Hecho
from .linix_historico import ComponenteSalarialPiloto


@dataclass(frozen=True, repr=False)
class DeclaracionAlcanceIngresosPiloto:
    documento: str
    digest_s14: str
    digest_corroboracion: str
    fecha_declaracion: date
    referencia: str

    def __post_init__(self):
        if (type(self.documento) is not str or not re.fullmatch(r"[0-9]{1,30}", self.documento)
                or type(self.fecha_declaracion) is not date
                or any(type(v) is not str or not re.fullmatch(r"[a-f0-9]{64}", v)
                       for v in (self.digest_s14, self.digest_corroboracion, self.referencia))):
            raise ValueError("DECLARACION_ALCANCE_INGRESOS_INVALIDA")


def cargar_declaracion_alcance_ingresos(path, *, documento):
    try:
        raw = Path(path).read_bytes()
        p = json.loads(raw)
        exactos = {"schema": "credihoy-alcance-ingresos-piloto-v2", "numeroDocumento": documento,
            "fuenteComputablePropuesta": "SALARIO_MENSUAL_LINIX", "declaraIngresoMensualTotal": False,
            "habilitacionPoliticaIngresoComputable": "PENDIENTE_VALIDACION",
            "precisionFecha": "DIA", "procedencia": "DECLARACION_EXPRESA_TITULAR_EN_CHAT",
            "alcance": "EXPEDIENTE_PILOTO_UNICO", "autorizaDecisionCrediticia": False,
            "autorizaRadicacionLinix": False}
        if (type(p) is not dict or set(p) != set(exactos) | {
                "fechaDeclaracion", "evidenciaS14Sha256", "corroboracionSha256", "otrosIngresos", "reemplazaSha256"}
                or any(type(p[k]) is not type(v) or p[k] != v for k, v in exactos.items())):
            raise ValueError
        otros = {"existen": True, "incluidosEnPiloto": False, "valor": None,
            "motivo": "EXCLUIDOS_DEL_PILOTO_NO_REGISTRADOS_EN_LINIX"}
        if (type(p["otrosIngresos"]) is not dict or set(p["otrosIngresos"]) != set(otros)
                or any(type(p["otrosIngresos"][k]) is not type(v) or p["otrosIngresos"][k] != v for k, v in otros.items())
                or type(p["reemplazaSha256"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", p["reemplazaSha256"])):
            raise ValueError
        return DeclaracionAlcanceIngresosPiloto(documento, p["evidenciaS14Sha256"],
            p["corroboracionSha256"], date.fromisoformat(p["fechaDeclaracion"]), hashlib.sha256(raw).hexdigest())
    except Exception:
        raise ValueError("DECLARACION_ALCANCE_INGRESOS_INVALIDA") from None


def ingreso_utilizable_piloto(componente, declaracion, *, documento, fecha, habilitacion_politica=None):
    """El titular propone el alcance; la politica de ingresos habilita el operando."""
    if (type(componente) is not ComponenteSalarialPiloto
            or type(declaracion) is not DeclaracionAlcanceIngresosPiloto
            or type(fecha) is not datetime or fecha.utcoffset() is None
            or declaracion.documento != documento
            or declaracion.digest_s14 != componente.salario_mensual.referencia_evidencia
            or declaracion.digest_corroboracion != componente.digest_confirmacion):
        raise ValueError("INGRESO_PILOTO_IDENTIDAD_O_PROCEDENCIA_INVALIDA")
    h = componente.salario_mensual
    if (h.fecha_consulta > fecha or componente.fecha_confirmacion > fecha.date()
            or declaracion.fecha_declaracion > fecha.date()
            or declaracion.fecha_declaracion < componente.fecha_confirmacion):
        return Hecho(fuente=h.fuente, observacion="INGRESO_PILOTO_POSTERIOR_AL_CORTE")
    p = habilitacion_politica
    if (not isinstance(p, Hecho) or not p.disponible or p.valor is not True
            or p.estado_certificacion != EC.CERTIFICADO or not p.fuente or not p.ruta_origen
            or not p.referencia_evidencia or p.fecha_consulta is None or p.fecha_consulta > fecha):
        return Hecho(fuente=h.fuente, referencia_evidencia=declaracion.referencia,
            ruta_origen=h.ruta_origen, observacion="SALARIO_COMPUTABLE_PROPUESTO_PILOTO; "
            "HABILITACION_POLITICA_INGRESOS_PENDIENTE; otros ingresos existentes excluidos, importe desconocido")
    # Nunca convierte OBSERVADO en CERTIFICADO ni declara ingreso total real.
    return replace(h, observacion="SALARIO_COMPUTABLE_PILOTO; otros ingresos existentes excluidos "
        "sin importe; declaracion=" + declaracion.referencia + "; habilitacion=" + p.referencia_evidencia
        + "; no ingreso total real ni autorizacion crediticia")
