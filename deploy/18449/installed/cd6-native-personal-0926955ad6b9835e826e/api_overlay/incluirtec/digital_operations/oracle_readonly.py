"""Explicit Oracle read connector. No public SQL/transaction write capability."""

import argparse
from datetime import datetime
import json
import os
import re
from types import MappingProxyType
from xml.etree import ElementTree as ET

from .contracts import ProtocolError
from .credihoy import LINE, clob_text, numeric_id, reference_from_fields, xml_fields
from .credihoy_oracle import (
    ADMIN_USERS, ADMIN_CONTROLS_SQL, FLOW_BY_INTENT_SQL, CONTROLS_SQL, ERRORS_SQL, SESSION_SQL, TASKS_SQL,
)
from .credihoy_repository import (
    APPLICATION_BY_REFERENCE_SQL, RADICATION_BY_FLOW_SQL, REFERENCE_TASK_SQL, CredihoyReadRepository,
    IDENTITY_BY_DOCUMENT_SQL, PERSONAL_IDENTITY_NAME_SQL, PAYROLL_BY_HOLDER_SQL, DUPLICATE_APPLICATION_SQL,
    WORKFLOW_ERROR_DETAILS_SQL, PILOT_FINANCIAL_SQL, EVALUATION_TASK_SQL, CENTRAL_RISK_TASK_SQL,
    INSTITUTIONAL_APPLICATION_SQL, INSTITUTIONAL_TASKS_SQL, INSTITUTIONAL_PAGARE_SQL,
    NATIVE_SIGNING_TASKS_SQL,
    FINANCIAL_SCHEMA_SQL, FINANCIAL_FK_SQL, FINANCIAL_APPLICATION_SQL, FINANCIAL_CONFIG_SQL,
    FINANCIAL_LEDGER_SQL, FINANCIAL_DETAIL_SQL, FINANCIAL_PREVIOUS_SQL,
    SAVINGS_BY_VOUCHER_SQL, SAVINGS_ACCOUNT_CUT_SQL,
)
from .production_preflight import QUERIES, ROUTE_DETAIL_SQL, ROUTE_PATHS_SQL


READ_ONLY_SQL = "SET TRANSACTION READ ONLY"
PROBE_NUMBER = 17
PROBE_XML = "<PruebaCD4H><Estado>LECTURA</Estado></PruebaCD4H>"
CLOCK_SQL = "SELECT SYSTIMESTAMP FROM DUAL"
TYPE_PROBE_SQL = """
SELECT CAST(:probe_number AS NUMBER), XMLSERIALIZE(CONTENT XMLTYPE(:probe_xml) AS CLOB)
FROM DUAL
"""
XML_COLUMNS_SQL = """
SELECT COLUMN_NAME, DATA_TYPE
FROM ALL_TAB_COLUMNS
WHERE OWNER = 'LINIX' AND TABLE_NAME = 'CE8002TREPOSIT'
  AND COLUMN_NAME IN ('N_XML_ENT', 'N_XML_ENT_NEW', 'N_XML_SAL')
ORDER BY COLUMN_NAME
"""
SIGNATURE_SQL = """
SELECT OVERLOAD, POSITION, ARGUMENT_NAME, IN_OUT, DATA_TYPE, TYPE_OWNER, TYPE_NAME
FROM ALL_ARGUMENTS
WHERE OWNER = 'LINIX' AND PACKAGE_NAME = 'PK_WF_FLUJO'
  AND OBJECT_NAME = 'PR_INICIAR_FLUJO_RET' AND DATA_LEVEL = 0
ORDER BY OVERLOAD, POSITION
"""
SERIALIZED_RADICATION_SQL = RADICATION_BY_FLOW_SQL
ACCESS_SQL = {
    "WF1710MFLUJO": "SELECT K_FLUJO, K_MODELO FROM LINIX.WF1710MFLUJO WHERE 1=0",
    "WF1711D0000": "SELECT K_FLUJO, K_SERIE, K_IDACTI, K_IDRUTA, I_ESTTAR, K_REPOSI FROM LINIX.WF1711D0000 WHERE 1=0",
    "WF1706MACTIVID": "SELECT K_IDACTI, I_EJECUC FROM LINIX.WF1706MACTIVID WHERE 1=0",
    "WF1750DERREJEC": "SELECT K_FLUJO FROM LINIX.WF1750DERREJEC WHERE 1=0",
    "CE8002TREPOSIT": "SELECT K_REPOSI FROM LINIX.CE8002TREPOSIT WHERE 1=0",
    "CA090MGSOLCRED": "SELECT K_TIPODR, K_NUMDOC, K_IDTERC, K_MODALI, K_DESTIN, V_MONTO, I_APROBA, I_LIQUID, K_IDTERC_PRM FROM LINIX.CA090MGSOLCRED WHERE 1=0",
    "GR001MTERCERO": "SELECT K_IDTERC, A_NUMNIT FROM LINIX.GR001MTERCERO WHERE 1=0",
}
# Exact query texts/bind names; not a permissive 'starts with SELECT' filter.
QUERY_RULES = {
    sql.strip(): (sql, frozenset()) for sql in
    (SESSION_SQL, CLOCK_SQL, XML_COLUMNS_SQL, SIGNATURE_SQL, *ACCESS_SQL.values())
}
QUERY_RULES.update({
    **{query.strip(): (query, frozenset()) for query in QUERIES.values()},
    ROUTE_DETAIL_SQL.strip(): (ROUTE_DETAIL_SQL, frozenset({'route_id'})),
    ROUTE_PATHS_SQL.strip(): (ROUTE_PATHS_SQL, frozenset({'route_id'})),
    REFERENCE_TASK_SQL.strip(): (REFERENCE_TASK_SQL, frozenset({'flow_id'})),
    EVALUATION_TASK_SQL.strip(): (EVALUATION_TASK_SQL, frozenset({'flow_id'})),
    CENTRAL_RISK_TASK_SQL.strip(): (CENTRAL_RISK_TASK_SQL, frozenset({'flow_id'})),
    INSTITUTIONAL_APPLICATION_SQL.strip(): (INSTITUTIONAL_APPLICATION_SQL, frozenset({'s'})),
    INSTITUTIONAL_TASKS_SQL.strip(): (INSTITUTIONAL_TASKS_SQL, frozenset({'f'})),
    INSTITUTIONAL_PAGARE_SQL.strip(): (INSTITUTIONAL_PAGARE_SQL, frozenset({'t', 'p'})),
    NATIVE_SIGNING_TASKS_SQL.strip(): (NATIVE_SIGNING_TASKS_SQL, frozenset({'f'})),
    FINANCIAL_SCHEMA_SQL.strip(): (FINANCIAL_SCHEMA_SQL, frozenset()),
    FINANCIAL_FK_SQL.strip(): (FINANCIAL_FK_SQL, frozenset()),
    FINANCIAL_APPLICATION_SQL.strip(): (FINANCIAL_APPLICATION_SQL, frozenset({'s'})),
    FINANCIAL_CONFIG_SQL.strip(): (FINANCIAL_CONFIG_SQL, frozenset({'c'})),
    FINANCIAL_LEDGER_SQL.strip(): (FINANCIAL_LEDGER_SQL, frozenset({'y','b','t','n'})),
    FINANCIAL_PREVIOUS_SQL.strip(): (FINANCIAL_PREVIOUS_SQL, frozenset({'h','t','n'})),
    SAVINGS_BY_VOUCHER_SQL.strip(): (SAVINGS_BY_VOUCHER_SQL, frozenset({'b','t','n','d','u'})),
    SAVINGS_ACCOUNT_CUT_SQL.strip(): (SAVINGS_ACCOUNT_CUT_SQL, frozenset({'c','d','u'})),
    **{sql.strip():(sql,frozenset({'t','n'})) for sql in FINANCIAL_DETAIL_SQL.values()},
    WORKFLOW_ERROR_DETAILS_SQL.strip(): (WORKFLOW_ERROR_DETAILS_SQL, frozenset({'flow_id'})),
    IDENTITY_BY_DOCUMENT_SQL.strip(): (IDENTITY_BY_DOCUMENT_SQL, frozenset({'documento'})),
    PERSONAL_IDENTITY_NAME_SQL.strip(): (PERSONAL_IDENTITY_NAME_SQL, frozenset({'documento','tercero'})),
    PAYROLL_BY_HOLDER_SQL.strip(): (PAYROLL_BY_HOLDER_SQL, frozenset({'tercero'})),
    DUPLICATE_APPLICATION_SQL.strip(): (DUPLICATE_APPLICATION_SQL, frozenset({'tercero', 'monto', 'desde'})),
    RADICATION_BY_FLOW_SQL.strip(): (SERIALIZED_RADICATION_SQL, frozenset({"flow_id"})),
    TASKS_SQL.strip(): (TASKS_SQL, frozenset({"flow_id"})),
    ERRORS_SQL.strip(): (ERRORS_SQL, frozenset({"flow_id"})),
    APPLICATION_BY_REFERENCE_SQL.strip(): (APPLICATION_BY_REFERENCE_SQL,
        frozenset({"application_type", "application_number"})),
    CONTROLS_SQL.strip(): (CONTROLS_SQL, frozenset({"application_type", "application_number"})),
    ADMIN_CONTROLS_SQL.strip(): (ADMIN_CONTROLS_SQL, frozenset({"application_type", "application_number"})),
    FLOW_BY_INTENT_SQL.strip(): (FLOW_BY_INTENT_SQL, frozenset({"intent_name"})),
    TYPE_PROBE_SQL.strip(): (TYPE_PROBE_SQL, frozenset({"probe_number", "probe_xml"})),
})
QUERY_RULES.update({sql.strip(): (sql, frozenset({'tercero'})) for sql in PILOT_FINANCIAL_SQL.values()})
QUERY_RULES = MappingProxyType(QUERY_RULES)


def _infrastructure():
    # Lazy: importing this module never loads credentials or opens Oracle.
    from django.conf import settings
    from incluirtec.services.oracle import get_connection
    from incluirtec.services.oracle_driver import oracle_driver
    return get_connection, oracle_driver, settings.DATABASES["oracle"]["USER"]


def _technical_user(value):
    if not isinstance(value, str):
        return False
    name = value.strip().strip('"')
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]{0,127}", name)) and name.upper() not in ADMIN_USERS


def _cleanup_native(connection, cursor=None):
    if cursor is not None:
        try:
            cursor.close()
        except Exception:
            pass
    for method in (connection.rollback, connection.close):
        try:
            method()
        except Exception:
            pass


class ReadOnlyOracleCursor:
    def __init__(self, owner, native):
        self._owner = owner
        self._native = native
        self._closed = False

    def _check(self):
        self._owner._check()
        if self._closed:
            raise ProtocolError("CREDIHOY_CURSOR_CERRADO")

    @property
    def description(self):
        self._check()
        return self._native.description

    def execute(self, sql, parameters=None, **binds):
        self._check()
        rule = QUERY_RULES.get(sql.strip()) if isinstance(sql, str) else None
        if rule is None:
            raise ProtocolError("CREDIHOY_SQL_NO_PERMITIDO", 403)
        if parameters is not None and not isinstance(parameters, dict):
            raise ProtocolError("CREDIHOY_BINDS_INVALIDOS")
        if parameters and set(parameters) & set(binds):
            raise ProtocolError("CREDIHOY_BINDS_INVALIDOS")
        arguments = {**(parameters or {}), **binds}
        if set(arguments) != rule[1]:
            raise ProtocolError("CREDIHOY_BINDS_INVALIDOS")
        if sql.strip() == TYPE_PROBE_SQL.strip():
            try:
                number = arguments["probe_number"].getvalue()
                xml = clob_text(arguments["probe_xml"].getvalue())
            except Exception:
                raise ProtocolError("CREDIHOY_BINDS_INVALIDOS") from None
            # XMLTYPE receives only this fixed benign probe, never arbitrary entities.
            if number != PROBE_NUMBER or xml != PROBE_XML:
                raise ProtocolError("CREDIHOY_BINDS_INVALIDOS")
        self._call(self._native.execute, rule[0], arguments)

    def _call(self, method, *args):
        self._check()
        try:
            return method(*args)
        except TimeoutError:
            raise ProtocolError("CREDIHOY_ORACLE_TIEMPO_AGOTADO", 503) from None
        except Exception:
            raise ProtocolError("CREDIHOY_ORACLE_LECTURA_FALLIDA", 503) from None

    def var(self, kind):
        if kind not in (self._owner.number_type, self._owner.clob_type):
            raise ProtocolError("CREDIHOY_TIPO_BIND_NO_PERMITIDO")
        return self._call(self._native.var, kind)

    def fetchone(self):
        return self._call(self._native.fetchone)

    def fetchall(self):
        return self._call(self._native.fetchall)

    def fetchmany(self, size):
        return self._call(self._native.fetchmany, size)

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._native.close()
            except Exception:
                raise ProtocolError("CREDIHOY_CURSOR_CIERRE_INCIERTO", 503) from None


class ReadOnlyOracleConnection:
    def __init__(self, native, driver):
        self._native = native
        self.number_type = driver.NUMBER
        self.clob_type = driver.CLOB
        self._closed = False
        self._rollback_attempted = False
        self._cursors = []

    def _check(self):
        if self._closed or self._rollback_attempted:
            raise ProtocolError("CREDIHOY_SESION_LECTURA_TERMINADA")

    @property
    def autocommit(self):
        return False

    @autocommit.setter
    def autocommit(self, value):
        self._check()
        if value is not False:
            raise ProtocolError("CREDIHOY_MUTACION_NO_AUTORIZADA", 403)

    def cursor(self):
        self._check()
        try:
            cursor = ReadOnlyOracleCursor(self, self._native.cursor())
        except Exception:
            raise ProtocolError("CREDIHOY_ORACLE_LECTURA_FALLIDA", 503) from None
        self._cursors.append(cursor)
        return cursor

    def commit(self):
        raise ProtocolError("CREDIHOY_MUTACION_NO_AUTORIZADA", 403)

    def rollback(self):
        self._check()
        self._rollback_attempted = True
        try:
            self._native.rollback()
        except Exception:
            raise ProtocolError("CREDIHOY_ROLLBACK_INCIERTO", 503) from None

    def close(self):
        if self._closed:
            return
        failed = False
        for cursor in self._cursors:
            try:
                cursor.close()
            except ProtocolError:
                failed = True
        if not self._rollback_attempted:
            try:
                self.rollback()
            except ProtocolError:
                failed = True
        self._closed = True
        try:
            self._native.close()
        except Exception:
            failed = True
        if failed:
            raise ProtocolError("CREDIHOY_SESION_CIERRE_INCIERTO", 503) from None

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, kind, value, traceback):
        try:
            self.close()
        except ProtocolError:
            if kind is None:
                raise


class RealOracleReadPort:
    """Existing application connector behind a strictly read-only capability."""

    synthetic = False

    def __init__(self, *, call_timeout_ms=10000):
        if type(call_timeout_ms) is not int or not 1000 <= call_timeout_ms <= 120000:
            raise ProtocolError("CREDIHOY_TIMEOUT_INVALIDO")
        self.call_timeout_ms = call_timeout_ms
        self.driver_name = None
        self.driver_version = None
        self.number_type = None
        self.clob_type = None

    def open_connection(self):
        native, cursor = None, None
        try:
            factory, driver, configured_user = _infrastructure()
            if not _technical_user(configured_user):
                raise ProtocolError("CREDIHOY_USUARIO_TECNICO_REQUERIDO")
            if driver.__name__ not in {"cx_Oracle", "oracledb"}:
                raise ProtocolError("CREDIHOY_DRIVER_NO_COMPATIBLE")
            native = factory()
            native.autocommit = False
            if native.autocommit is not False:
                raise ProtocolError("CREDIHOY_AUTOCOMMIT_NO_CONTROLADO")
            timeout_attribute = "callTimeout" if driver.__name__ == "cx_Oracle" else "call_timeout"
            setattr(native, timeout_attribute, self.call_timeout_ms)
            if getattr(native, timeout_attribute) != self.call_timeout_ms:
                raise ProtocolError("CREDIHOY_TIMEOUT_NO_CONTROLADO")
            cursor = native.cursor()
            cursor.execute(SESSION_SQL)
            users = cursor.fetchone()
            if not users or len(users) != 2 or not all(_technical_user(user) for user in users):
                raise ProtocolError("CREDIHOY_USUARIO_TECNICO_REQUERIDO")
            native.rollback()  # End identity probe before the first read-only transaction.
            cursor.execute(READ_ONLY_SQL)
            cursor.close()
            cursor = None
            self.driver_name = driver.__name__
            self.driver_version = str(driver.__version__)
            self.number_type, self.clob_type = driver.NUMBER, driver.CLOB
            return ReadOnlyOracleConnection(native, driver)
        except ProtocolError:
            if native is not None:
                _cleanup_native(native, cursor)
            raise
        except Exception:
            if native is not None:
                _cleanup_native(native, cursor)
            raise ProtocolError("CREDIHOY_ORACLE_CONEXION_NO_DISPONIBLE", 503) from None


def signature_matches(rows):
    expected = (
        "N_K_PROCES", "N_K_MODELO", "N_FLUJO", "F_INICIO", "N_K_USUARI",
        "N_XML_ENT", "N_REFERE", "N_MODAPL", "N_K_SUCURS", "N_I_COMMIT", "V_K_FLUJO",
    )
    if not isinstance(rows, (list, tuple)) or len(rows) != len(expected):
        return False
    for row in rows:
        if (not isinstance(row, (list, tuple)) or len(row) != 7
                or type(row[1]) is not int
                or (row[0] is not None and not isinstance(row[0], str))
                or any(value is not None and not isinstance(value, str) for value in row[4:])):
            return False
    if len({row[0] for row in rows}) != 1:
        return False
    xml_types = {
        ("XMLTYPE", None, None),
        ("XMLTYPE", "SYS", "XMLTYPE"),
        ("OBJECT", "SYS", "XMLTYPE"),
        # ALL_ARGUMENTS reports the public Oracle XMLTYPE through this opaque type.
        ("OPAQUE/XMLTYPE", "PUBLIC", "XMLTYPE"),
    }
    for position, (entry, name) in enumerate(zip(sorted(rows, key=lambda row: row[1]), expected), 1):
        _, actual_position, actual_name, direction, kind, owner, type_name = entry
        if (actual_position != position or actual_name != name
                or direction != ("OUT" if position == 11 else "IN")
                or not isinstance(kind, str)):
            return False
        kind = kind.upper()
        if position == 6:
            if (kind, owner, type_name) not in xml_types:
                return False
        elif kind != ("DATE" if position == 4 else "NUMBER" if position == 11 else "VARCHAR2"):
            return False
    return True


def check_connectivity(port, *, flow_id=None, application_number=None):
    """Returns operational indicators only; never prints XML, documents or names."""
    if not isinstance(port, RealOracleReadPort):
        raise ProtocolError("CREDIHOY_PUERTO_LECTURA_REQUERIDO")
    if flow_id is not None:
        flow_id = numeric_id(flow_id)
    if application_number is not None:
        application_number = numeric_id(application_number)
    try:
        with port.open_connection() as connection:
            cursor = connection.cursor()
            cursor.execute(SESSION_SQL)
            users = cursor.fetchone()
            cursor.execute(CLOCK_SQL)
            date = cursor.fetchone()[0]
            if not isinstance(date, datetime):
                raise ProtocolError("CREDIHOY_FECHA_SERVIDOR_NO_VERIFICADA")
            for sql in ACCESS_SQL.values():
                cursor.execute(sql)
                if cursor.fetchall():
                    raise ProtocolError("CREDIHOY_PROBE_INESPERADO")
            cursor.execute(XML_COLUMNS_SQL)
            types = dict(cursor.fetchall())
            if types != {name: "XMLTYPE" for name in ("N_XML_ENT", "N_XML_ENT_NEW", "N_XML_SAL")}:
                raise ProtocolError("CREDIHOY_TIPOS_XML_NO_VERIFICADOS")
            number = cursor.var(port.number_type)
            xml = cursor.var(port.clob_type)
            number.setvalue(0, PROBE_NUMBER)
            xml.setvalue(0, PROBE_XML)
            cursor.execute(TYPE_PROBE_SQL, probe_number=number, probe_xml=xml)
            number_value, xml_value = cursor.fetchone()
            if number_value != PROBE_NUMBER or ET.fromstring(clob_text(xml_value)).findtext("Estado") != "LECTURA":
                raise ProtocolError("CREDIHOY_BINDS_NO_VERIFICADOS")
            for sql, binds in (
                (RADICATION_BY_FLOW_SQL, {"flow_id": -1}), (TASKS_SQL, {"flow_id": -1}),
                (APPLICATION_BY_REFERENCE_SQL, {"application_type": "SC", "application_number": "0"}),
                (CONTROLS_SQL, {"application_type": "SC", "application_number": "0"}),
            ):
                cursor.execute(sql, binds)
                if cursor.fetchall():
                    raise ProtocolError("CREDIHOY_PROBE_INESPERADO")
            cursor.execute(ERRORS_SQL, flow_id=-1)
            cursor.fetchone()
            cursor.execute(SIGNATURE_SQL)
            signature = signature_matches(cursor.fetchall())
            result = {"result": 0, "soloLectura": True, "driver": port.driver_name,
                "versionDriver": port.driver_version, "usuarioSesion": users[0],
                "fechaServidor": date.isoformat(), "tablasLecturaVerificadas": list(ACCESS_SQL),
                "selectsCompatibles": True, "bindsNumberClobVerificados": True,
                "firmaInicioVerificada": signature, "requiereRevision": not signature,
                "inicioWorkflowHabilitado": False}
            repository = CredihoyReadRepository(cursor)
            references = set()
            if flow_id is not None:
                records = repository.radication_records(flow_id)
                result["registros113"] = len(records)
                for record in records:
                    profile = xml_fields(record.xml_input)
                    if record.workflow != LINE.workflow_model or any(profile.get(key) != value for key, value in (
                        ("A_PRODUCTO", "390"), ("A_MODALIDAD_CREDITO", "111"), ("A_DESTINACION", "CFA"))):
                        raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)
                    first = reference_from_fields(xml_fields(record.xml_new))
                    second = reference_from_fields(xml_fields(record.xml_output))
                    if first != second:
                        raise ProtocolError("CREDIHOY_XML_CONTRADICTORIO", 409)
                    references.add(first)
                if len(references) != 1:
                    raise ProtocolError("CREDIHOY_SC_NO_CONFIRMADA")
            if application_number is not None:
                from .credihoy import ScReference
                supplied = ScReference("SC", application_number)
                if references and supplied not in references:
                    raise ProtocolError("CREDIHOY_XML_CONTRADICTORIO", 409)
                references.add(supplied)
            if references:
                rows = repository.application_rows(next(iter(references)))
                if len(rows) != 1:
                    raise ProtocolError("CREDIHOY_SC_NO_CONFIRMADA")
                if str(rows[0]["K_MODALI"]) != "111" or rows[0]["K_DESTIN"] != "CFA":
                    raise ProtocolError("CREDIHOY_CORRESPONDENCIA_INVALIDA", 409)
                result["solicitudExistenteVerificada"] = True
        result["sesionCerrada"] = True
        return result
    except ProtocolError:
        raise
    except Exception:
        raise ProtocolError("CREDIHOY_COMPROBACION_LECTURA_FALLIDA", 503) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="CD-4H: comprobacion Oracle exclusivamente de lectura")
    parser.add_argument("--connect-readonly", action="store_true")
    parser.add_argument("--flow", help="K_FLUJO existente autorizado para lectura")
    parser.add_argument("--solicitud", help="Numero SC existente autorizado para lectura")
    parser.add_argument("--call-timeout-ms", type=int, default=10000)
    args = parser.parse_args(argv)
    if not args.connect_readonly:
        print(json.dumps({"result": 1, "codigo": "CONEXION_NO_SOLICITADA", "inicioWorkflowHabilitado": False}))
        return 2
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core_api.settings")
    try:
        result = check_connectivity(RealOracleReadPort(call_timeout_ms=args.call_timeout_ms),
                                    flow_id=args.flow, application_number=args.solicitud)
    except ProtocolError as exc:
        print(json.dumps({"result": 1, "codigo": exc.code, "inicioWorkflowHabilitado": False}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
