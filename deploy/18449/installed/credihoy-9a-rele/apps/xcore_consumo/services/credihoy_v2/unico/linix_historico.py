"""Salidas manuales S01-S05: solo hechos locales, nunca consultas LINIX."""

import calendar
import csv
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
import hashlib
import io
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from ..base import EstadoCertificacion as EC, Hecho
from ..hechos import (
    AportesSociales203V2, CarteraAlCierreV2, CatalogoInternoV2,
    CandidatoCuotaInternaCrediHoyV2, DocumentoAporte203V2, HechosInternosCrediHoyV2,
    ObligacionCierreV2, ObligacionInternaCrediHoyV2, ObligacionesActualesV2,
    SolicitudOrigenInternaV2,
)
from ..internos_http import decimal_exacto, fecha_hora, fecha_registrada

COLS = {
    1: "K_IDTERC A_NUMNIT GR001_I_ESTADO F_NACIMI I_ESTASO I_ESTCLI I_ASOCIA F_PRIAFI F_ANTIGU F_ULTAFI".split(),
    2: ("K_IDTERC K_TIPODR K_NUMDOC A_TIPODR A_OBLIGA K_MODALI N_MODALIDAD K_LINEA N_LINEA "
        "I_ESTSOL I_APROBA I_LIQUID I_CANCEL I_ANULAD F_PRESTA F_CANCEL V_SALDO "
        "PLAN_TIPO PLAN_NUMERO K_SERIE K_IDREES I_TIPCUO V_CUOTA Q_CUOTA I_PERPAG F_CUOTA").split(),
    3: "K_PERANO K_ANO A_NUMPER N_PERANO".split(),
    4: "K_IDTERC A_TIPODR A_OBLIGA K_PERANO Q_CLASIF Q_DIAMORA Q_DIAVEN V_SALMOR V_SALKAP".split(),
    5: "K_NUMDOC K_TIPODR K_IDTERC I_CANCEL F_VIGENC".split(),
}
S14_SQL = "SELECT K_IDTERC, V_SUELDO\nFROM LINIX.AP014MCLIENTE\nWHERE K_IDTERC = :id_tercero;"
S14_RUTA = "LINIX.AP014MCLIENTE.V_SUELDO"
SALARIO_CORROBORADO_PILOTO = "INGRESO_SALARIAL_MENSUAL_CORROBORADO_PILOTO"


@dataclass(frozen=True, repr=False)
class ComponenteSalarialPiloto:
    salario_mensual: Hecho[Decimal]
    fecha_confirmacion: date
    digest_confirmacion: str

    def __post_init__(self):
        h = self.salario_mensual
        if (not isinstance(h, Hecho) or not h.disponible or type(h.valor) is not Decimal
                or h.estado_certificacion != EC.OBSERVADO or h.ruta_origen != S14_RUTA
                or h.fecha_consulta is None or type(self.fecha_confirmacion) is not date
                or not re.fullmatch(r"[a-f0-9]{64}", self.digest_confirmacion)):
            raise ValueError("COMPONENTE_SALARIAL_PILOTO_INVALIDO")

    def resumen(self):
        return {"clasificacion": SALARIO_CORROBORADO_PILOTO, "alcance": "EXPEDIENTE_PILOTO_UNICO",
            "fechaConfirmacion": self.fecha_confirmacion.isoformat(), "precisionFecha": "DIA",
            "utilizableComoComponenteSalarial": True, "declaraIngresoMensualTotal": False,
            "autorizaDecisionCrediticia": False, "autorizaRadicacionLinix": False}


def _cargar_confirmacion_salarial(path, tercero, sueldo, digest_s14):
    from .historico import _archivo, HistoricoInvalido
    try:
        p, digest = _archivo(path)
        exactos = {"schema": "cd5a-corroboracion-salarial-v1", "clasificacion": SALARIO_CORROBORADO_PILOTO,
            "alcance": "EXPEDIENTE_PILOTO_UNICO", "terceroLinix": tercero, "evidenciaS14Sha256": digest_s14,
            "precisionFecha": "DIA", "confirmadoPor": "TITULAR_OPERADOR", "procedencia": "CONFIRMACION_EXPRESA_EN_CHAT",
            "periodicidad": "MENSUAL", "declaraIngresoMensualTotal": False,
            "autorizaDecisionCrediticia": False, "autorizaRadicacionLinix": False}
        if (set(p) != set(exactos) | {"valorSalarialMensual", "fechaConfirmacion"}
                or any(type(p[k]) is not type(v) or p[k] != v for k, v in exactos.items())
                or type(p["valorSalarialMensual"]) is not str
                or not sueldo.disponible or decimal_exacto(p["valorSalarialMensual"]) != sueldo.valor
                or type(p["fechaConfirmacion"]) is not str
                or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", p["fechaConfirmacion"])):
            raise ValueError
        fecha = date.fromisoformat(p["fechaConfirmacion"])
        if fecha < sueldo.fecha_consulta.date():
            raise ValueError
        return ComponenteSalarialPiloto(replace(sueldo, observacion=SALARIO_CORROBORADO_PILOTO
            + "; componente salarial, no ingreso mensual total; solo expediente piloto"), fecha, digest)
    except Exception:
        raise HistoricoInvalido("CONFIRMACION_SALARIAL_PILOTO_INVALIDA") from None


def _cargar_s14(path, tercero):
    from .historico import _archivo, HistoricoInvalido
    try:
        p, digest = _archivo(path)
        if (set(p) != {"schema", "procedencia", "consulta", "sql", "rutaOrigen", "fechaEjecucion", "zonaHoraria", "filas"}
                or p["schema"] != "cd5a-linix-s14-v1" or p["consulta"] != "S14"
                or p["procedencia"] != "RESULTADO_COMUNICADO_POR_OPERADOR"
                or p["sql"] != S14_SQL or p["rutaOrigen"] != S14_RUTA
                or p["zonaHoraria"] != "America/Bogota"
                or not isinstance(p["filas"], list) or len(p["filas"]) != 1):
            raise ValueError
        row = p["filas"][0]
        if (not isinstance(row, dict) or set(row) != {"K_IDTERC", "V_SUELDO"} or row["K_IDTERC"] != tercero
                or (row["V_SUELDO"] is not None and type(row["V_SUELDO"]) is not str)):
            raise ValueError
        fecha = fecha_hora(p["fechaEjecucion"])
        if fecha.utcoffset() != fecha.astimezone(ZoneInfo(p["zonaHoraria"])).utcoffset():
            raise ValueError
        valor = None if row["V_SUELDO"] is None else decimal_exacto(row["V_SUELDO"])
        return Hecho(valor=valor, disponible=valor is not None, fuente="LINIX_SELECT_MANUAL_S14",
            referencia_evidencia=digest, ruta_origen=S14_RUTA, fecha_consulta=fecha,
            estado_certificacion=EC.OBSERVADO if valor is not None else EC.NO_DISPONIBLE,
            observacion="Resultado comunicado por operador; salario registrado, no ingreso mensual homologado"), digest
    except Exception:
        raise HistoricoInvalido("EVIDENCIA_S14_INVALIDA") from None


def _fecha_exportada(value):
    # DBeaver renders Oracle timestamps with a space; never attach an invented zone.
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?", value):
        value = value.replace(" ", "T", 1)
    return fecha_registrada(value)


@dataclass(frozen=True, repr=False)
class EvidenciaLinixHistorica:
    hechos: HechosInternosCrediHoyV2
    obligaciones: tuple[ObligacionInternaCrediHoyV2, ...]
    estados_asociado: tuple[tuple[str, Hecho[str]], ...]
    digest: str
    fecha_consulta: datetime | None
    advertencias: tuple[str, ...]
    filas_inventario: int
    digest_s14: str | None = None
    componente_salarial_piloto: ComponenteSalarialPiloto | None = None

    @property
    def huella(self):
        return hashlib.sha256((self.digest + (self.fecha_consulta.isoformat() if self.fecha_consulta else
                                            "NO_DECLARADA") + (self.digest_s14 or "")
                               + (self.componente_salarial_piloto.digest_confirmacion
                                  if self.componente_salarial_piloto else "")).encode()).hexdigest()

    def resumen(self):
        result = {"fuente": "LINIX_SELECT_MANUAL_S01_S05", "fechaConsulta":
            self.fecha_consulta.isoformat() if self.fecha_consulta else None,
            "periodo": self.hechos.periodo_anterior.valor,
            "obligacionesAlCierre": len(self.hechos.cartera_al_cierre.obligaciones),
            "documentos203": len(self.hechos.aportes_documentos_203),
            "filasInventario": self.filas_inventario, "obligacionesObservadas": len(self.obligaciones),
            "credihoy111Observado": any(o.modalidad.codigo.valor == "111" for o in self.obligaciones),
            "saldoAportesUtilizable": False, "vigenciaActualCertificada": False,
            "advertencias": list(self.advertencias)}
        if self.digest_s14 is not None:
            sueldo = self.hechos.sueldo_registrado
            result["sueldoRegistrado"] = {"disponible": sueldo.disponible,
                "estadoCertificacion": sueldo.estado_certificacion.value, "rutaOrigen": S14_RUTA,
                "fechaConsulta": sueldo.fecha_consulta.isoformat(), "zonaHoraria": "America/Bogota",
                "utilizableComoIngresoMensual": False, "motivo": "HOMOLOGACION_INGRESO_MENSUAL_PENDIENTE"}
            if self.componente_salarial_piloto is not None:
                result["sueldoRegistrado"]["corroboracionPiloto"] = self.componente_salarial_piloto.resumen()
                result["sueldoRegistrado"]["motivo"] = "COMPONENTE_SALARIAL_NO_EQUIVALE_INGRESO_TOTAL"
        return result


def _leer(path):
    from django.conf import settings
    from .historico import HistoricoInvalido
    try:
        p = Path(path).resolve(strict=True)
        if (str(p).startswith(("\\\\", "//")) or not p.is_file() or p.stat().st_size > 2_000_000
                or p.is_relative_to(Path(settings.BASE_DIR).resolve().parent)
                or any((d / ".git").exists() for d in p.parents)):
            raise ValueError
        raw = p.read_bytes()
        sections, current = {}, None
        for line in raw.decode("utf-8-sig").splitlines(keepends=True):
            marker = re.fullmatch(r"SQL ([1-5]):", line.strip())
            if marker:
                current = int(marker[1])
                if current in sections:
                    raise ValueError
                sections[current] = []
            elif line.strip():
                if current is None:
                    raise ValueError
                sections[current].append(line)
        if set(sections) != set(COLS):
            raise ValueError
        rows = {}
        missing = False
        for n, lines in sections.items():
            rows[n] = []
            for row in csv.reader(io.StringIO("".join(lines)), delimiter="\t", strict=True):
                # A missing final cell is NOT a certified null source column.
                if n == 5 and len(row) == 4:
                    row.append("")
                    missing = True
                if len(row) != len(COLS[n]):
                    raise ValueError
                rows[n].append(dict(zip(COLS[n], (v if v != "" else None for v in row))))
        return rows, hashlib.sha256(raw).hexdigest(), missing
    except Exception:
        raise HistoricoInvalido("EVIDENCIA_LINIX_FORMATO_INVALIDO") from None


def cargar_linix_historico(path, *, documento, fecha_evaluacion, fecha_consulta=None, s14_path=None,
                          confirmacion_salarial_path=None):
    from .historico import HistoricoInvalido
    try:
        rows, digest, missing = _leer(path)
        if fecha_evaluacion.utcoffset() is None:
            raise ValueError
        if fecha_consulta is not None:
            fecha_consulta = fecha_hora(fecha_consulta) if isinstance(fecha_consulta, str) else fecha_consulta
            if not isinstance(fecha_consulta, datetime) or fecha_consulta.utcoffset() is None:
                raise ValueError
        if len(rows[1]) != 1 or len(rows[3]) != 1:
            raise ValueError
        person, period = rows[1][0], rows[3][0]
        if person["A_NUMNIT"] != documento or not re.fullmatch(r"[0-9]{1,30}", person["K_IDTERC"] or ""):
            raise ValueError
        tercero = person["K_IDTERC"]
        sueldo, digest_s14 = (_cargar_s14(s14_path, tercero) if s14_path is not None else
                             (Hecho(fuente="S14_NO_IMPORTADO"), None))
        componente = (_cargar_confirmacion_salarial(confirmacion_salarial_path, tercero, sueldo, digest_s14)
                      if confirmacion_salarial_path is not None else None)
        if any(row["K_IDTERC"] != tercero for n in (2, 4, 5) for row in rows[n]):
            raise ValueError
        mes = fecha_evaluacion.month - 1 or 12
        ano = fecha_evaluacion.year - (fecha_evaluacion.month == 1)
        esperado = f"{ano:04d}{mes:02d}"
        if (period["K_PERANO"] != esperado or period["K_ANO"] != str(ano)
                or period["A_NUMPER"] != str(mes)
                or any(r["K_PERANO"] != esperado for r in rows[4])):
            raise ValueError
        corte = date(ano, mes, calendar.monthrange(ano, mes)[1])

        def hecho(value, ruta, parser=str, *, estado=EC.OBSERVADO, fecha_corte=None):
            return Hecho(valor=None if value is None else parser(value), disponible=value is not None,
                fuente="LINIX_SELECT_MANUAL", referencia_evidencia=digest, ruta_origen=ruta,
                fecha_corte=fecha_corte, fecha_consulta=fecha_consulta,
                estado_certificacion=estado if value is not None else EC.NO_DISPONIBLE,
                observacion="Registro al alcance del SELECT; no certifica vigencia ni decision")

        def entero(value):
            if not re.fullmatch(r"[0-9]+", value):
                raise ValueError
            return int(value)

        def no_certificado(ruta):
            return Hecho(fuente="LINIX_SELECT_MANUAL", referencia_evidencia=digest, ruta_origen=ruta,
                fecha_consulta=fecha_consulta, estado_certificacion=EC.NO_CERTIFICADO)

        prev = hecho(esperado, "S03.GR034TPERIODO.K_PERANO", estado=EC.CERTIFICADO, fecha_corte=corte)
        obligaciones_cierre = tuple(ObligacionCierreV2(**{
            key: hecho(r[column], "S04.CA179TPROCARTE." + column, parser,
                       estado=EC.CERTIFICADO, fecha_corte=corte)
            for key, column, parser in (
                ("tipo_documento", "A_TIPODR", str), ("referencia", "A_OBLIGA", str),
                ("clasificacion", "Q_CLASIF", str), ("dias_mora", "Q_DIAMORA", entero),
                ("dias_vencidos", "Q_DIAVEN", entero), ("saldo_mora", "V_SALMOR", decimal_exacto),
                ("saldo_capital", "V_SALKAP", decimal_exacto))}) for r in rows[4])
        cartera = CarteraAlCierreV2(periodo_anterior=prev, estado_cierre_operativo=EC.NO_CERTIFICADO,
            estado="SIN_REGISTROS_AL_CORTE" if not obligaciones_cierre else
                "PARCIAL" if any(v is None for r in rows[4] for v in r.values()) else "DISPONIBLE",
            obligaciones=obligaciones_cierre)
        documents = []
        for r in rows[5]:
            if r["K_TIPODR"] != "203":
                raise ValueError
            documents.append(DocumentoAporte203V2(
                referencia=hecho(r["K_NUMDOC"], "S05.DR041MGDOCUMEN.K_NUMDOC"),
                tipo_documento=hecho(r["K_TIPODR"], "S05.DR041MGDOCUMEN.K_TIPODR"),
                saldo_observado=no_certificado("S05.SALDO_NO_CONSULTADO"),
                cancelacion_registrada=hecho(r["I_CANCEL"], "S05.DR041MGDOCUMEN.I_CANCEL", estado=EC.SEMANTICA_PENDIENTE),
                fecha_vigencia_registrada=hecho(r["F_VIGENC"], "S05.DR041MGDOCUMEN.F_VIGENC", _fecha_exportada,
                                               estado=EC.SEMANTICA_PENDIENTE)))
        aportes = AportesSociales203V2(estado="SIN_REGISTROS_203" if not documents else
            "PARCIAL" if len(documents) == 1 else "AMBIGUO", estado_consolidacion="NO_CONSOLIDADO",
            documentos=tuple(documents), saldo_aportes_utilizable=no_certificado("aportesSociales203.saldoAportesUtilizable"))
        grupos = {}
        for r in rows[2]:
            key = (r["K_TIPODR"], r["K_NUMDOC"])
            if None in key:
                raise ValueError
            if key in grupos and any(r[k] != grupos[key][0][k] for k in COLS[2][:17]):
                raise ValueError
            grupos.setdefault(key, []).append(r)
        obligaciones = []
        for group in grupos.values():
            r = group[0]
            def h(column, parser=str):
                table = {"N_MODALIDAD": "CA079MGCONDICI", "N_LINEA": "CA081MLINEACRE"}.get(column, "CA090MGSOLCRED")
                source_column = "N_CONDIC" if column == "N_MODALIDAD" else column
                return hecho(r[column], "S02." + table + "." + source_column, parser)
            candidates = []
            for item in group:
                if all(item[k] is None for k in COLS[2][17:]):
                    continue
                if item["PLAN_TIPO"] != r["K_TIPODR"] or item["PLAN_NUMERO"] != r["K_NUMDOC"]:
                    raise ValueError
                candidates.append(CandidatoCuotaInternaCrediHoyV2(**{key: hecho(item[column],
                    "S02.CA093MDSOLCRED." + column, parser) for key, column, parser in (
                        ("serie_plan", "K_SERIE", str), ("reestructuracion", "K_IDREES", str),
                        ("tipo_cuota", "I_TIPCUO", str), ("cuota_contractual", "V_CUOTA", decimal_exacto),
                        ("numero_cuotas", "Q_CUOTA", entero), ("periodicidad", "I_PERPAG", str),
                        ("fecha_cuota", "F_CUOTA", _fecha_exportada))}))
            state = EC.AMBIGUO if len(candidates) > 1 else EC.OBSERVADO if candidates else EC.NO_DISPONIBLE
            general = {name: getattr(candidates[0], name) if len(candidates) == 1 else
                Hecho(fuente="LINIX_SELECT_MANUAL", referencia_evidencia=digest, estado_certificacion=state)
                for name in ("cuota_contractual", "numero_cuotas", "periodicidad", "fecha_cuota")}
            obligaciones.append(ObligacionInternaCrediHoyV2(tipo_obligacion=h("A_TIPODR"), numero_obligacion=h("A_OBLIGA"),
                solicitud_origen=SolicitudOrigenInternaV2(tipo=h("K_TIPODR"), numero=h("K_NUMDOC")),
                modalidad=CatalogoInternoV2(codigo=h("K_MODALI"), descripcion=h("N_MODALIDAD")),
                linea=CatalogoInternoV2(codigo=h("K_LINEA"), descripcion=h("N_LINEA")),
                estado_solicitud=h("I_ESTSOL"), aprobada=h("I_APROBA"), liquidada=h("I_LIQUID"),
                indicador_cancelacion=h("I_CANCEL"), indicador_anulacion=h("I_ANULAD"),
                fecha_prestamo=h("F_PRESTA", _fecha_exportada), fecha_cancelacion=h("F_CANCEL", _fecha_exportada),
                saldo_registrado=h("V_SALDO", decimal_exacto), estado_plan=state, cuotas_candidatas=tuple(candidates),
                **general))
        actuales = None if fecha_consulta is None else ObligacionesActualesV2(
            disponible=bool(obligaciones), obligaciones=tuple(obligaciones),
            estado_certificacion=EC.OBSERVADO if obligaciones else EC.NO_DISPONIBLE,
            estado="REGISTROS_OBSERVADOS" if obligaciones else "SIN_REGISTROS", temporalidad="REGISTROS_AL_CONSULTAR",
            fecha_corte=fecha_consulta, solicitud_evaluada_no_identificada=True,
            observacion="Inventario observado; sin cuota total ni vigencia inferida")
        facts = HechosInternosCrediHoyV2(periodo_anterior=prev, cartera_al_cierre=cartera,
            sueldo_registrado=sueldo,
            aportes_documentos_203=tuple(documents), aportes_sociales_203=aportes,
            saldo_aportes_utilizable=aportes.saldo_aportes_utilizable,
            fecha_nacimiento=hecho(person["F_NACIMI"], "S01.GR001MTERCERO.F_NACIMI", lambda v: _fecha_exportada(v).date()
                if isinstance(_fecha_exportada(v), datetime) else _fecha_exportada(v), estado=EC.CERTIFICADO),
            obligaciones_actuales=actuales, cuota_vigente_interna=no_certificado("cuotaVigenteInterna"))
        warnings = ["INGRESO_MENSUAL_NO_HOMOLOGADO", "SALDO_APORTES_NO_CERTIFICADO", "CALIFICACION_GLOBAL_NO_INFERIDA",
                    "INVENTARIO_NO_CERTIFICA_VIGENCIA_ACTUAL", "S01_ESTADOS_REGISTRADOS_SIN_SEMANTICA_FUNCIONAL"]
        if fecha_consulta is None:
            warnings.append("FECHA_CONSULTA_LINIX_NO_DECLARADA")
        if missing:
            warnings.append("S05_F_VIGENC_NO_EXPORTADA")
        if digest_s14 is not None:
            if componente is None:
                warnings.append("S14_SUELDO_OBSERVADO_NO_HOMOLOGADO_A_INGRESO_MENSUAL")
            else:
                warnings.remove("INGRESO_MENSUAL_NO_HOMOLOGADO")
                warnings.extend([SALARIO_CORROBORADO_PILOTO, "INGRESO_MENSUAL_TOTAL_NO_DISPONIBLE"])
            if sueldo.fecha_consulta > fecha_evaluacion:
                warnings.append("S14_POSTERIOR_A_FECHA_EVALUACION_ORIGINAL")
        return EvidenciaLinixHistorica(facts, tuple(obligaciones), tuple((k, hecho(person[k], "S01." + k))
            for k in COLS[1] if k not in {"K_IDTERC", "A_NUMNIT", "F_NACIMI"}), digest, fecha_consulta,
            tuple(warnings), len(rows[2]), digest_s14, componente)
    except HistoricoInvalido:
        raise
    except Exception:
        raise HistoricoInvalido("EVIDENCIA_LINIX_INCONSISTENTE") from None
