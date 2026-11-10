import logging
import os
from contextlib import closing
from datetime import date, datetime

from django.conf import settings

from apps.xcore_consumo.models import ConsultaCoreOracle, ConsultaEstamentoOracle

try:
    import oracledb
except ImportError:  # pragma: no cover
    oracledb = None


class OracleIntegrationError(Exception):
    pass


logger = logging.getLogger(__name__)
CAPACIDAD_PROCEDURE_NAME = "SP_CRCAPACIDAD"


def _oracle_enabled():
    raw_setting = getattr(settings, "XCORE_CONSUMO_ORACLE_ENABLED", None)
    raw = raw_setting if raw_setting is not None else os.getenv("XCORE_CONSUMO_ORACLE_ENABLED", "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "si"}


def _oracle_conf():
    return {
        "user": os.getenv("ORACLE_USER", ""),
        "password": os.getenv("ORACLE_PASSWORD", ""),
        "dsn": os.getenv("ORACLE_DSN", ""),
        "host": os.getenv("ORACLE_HOST", ""),
        "port": int(os.getenv("ORACLE_PORT", "1521")),
        "service_name": os.getenv("ORACLE_SERVICE_NAME", ""),
    }


def _connect():
    if not _oracle_enabled():
        raise OracleIntegrationError("Integracion Oracle deshabilitada en configuracion.")
    if not oracledb:
        raise OracleIntegrationError("Falta dependencia oracledb.")
    conf = _oracle_conf()
    dsn = conf["dsn"] or oracledb.makedsn(conf["host"], conf["port"], service_name=conf["service_name"])
    return oracledb.connect(user=conf["user"], password=conf["password"], dsn=dsn)


def _map_capacidad_row(row) -> dict:
    row_length = len(row)
    if row_length < 18:
        raise OracleIntegrationError(
            f"{CAPACIDAD_PROCEDURE_NAME} devolvio una fila con {row_length} columnas: {row}"
        )

    data = {
        "estrato": row[0],
        "nivel_estudios": row[1],
        "estado_civil": row[2],
        "genero": row[3],
        "tipo_vivienda": row[4],
        "forma_pago": row[5],
        "tipo_contrato": row[6],
        "numero_personas_cargo": row[7],
        "edad": row[8],
        "antiguedad_asociado": row[9],
        "ingresos": row[10],
        "aportes_sociales": row[11],
        "activos": row[12],
        "valor_activos": "",
        "pasivos": "",
        "valor_pasivos": 0,
        "saldo_creditos": "",
        "ocupacion": "",
        "zona": "",
        "nombre": "",
    }

    if row_length >= 19:
        data.update(
            {
                "valor_activos": row[13] or "",
                "pasivos": row[14] or "",
                "saldo_creditos": row[15],
                "ocupacion": row[16],
                "zona": row[17],
                "nombre": row[18],
            }
        )
    elif row_length == 18:
        data.update(
            {
                "valor_activos": row[13] or "",
                "pasivos": "",
                "saldo_creditos": row[13],
                "ocupacion": row[15],
                "zona": row[16],
                "nombre": row[17],
            }
        )

    if row_length >= 20:
        data["valor_pasivos"] = row[19] or 0

    if row_length >= 22:
        data.update(
            {
                "ocupacion": row[20],
                "zona": row[21],
                "nombre": row[22] if row_length >= 23 else data["nombre"],
            }
        )

    return data


def _format_document_issue_date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _normalize_credito_digital_message(value) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _extract_credito_digital_cursor_message(output_cursor) -> tuple[str, list]:
    rows = list(output_cursor or [])
    for row in rows:
        if isinstance(row, (list, tuple)):
            for cell in row:
                message = _normalize_credito_digital_message(cell)
                if message:
                    return message, rows
        else:
            message = _normalize_credito_digital_message(row)
            if message:
                return message, rows
    return "", rows


def _credito_digital_is_ok(message: str) -> bool:
    normalized = message.strip().upper()
    return normalized in {"OK", "VALIDACION EXITOSA", "VALIDACION EXITOSA"}


def validar_credito_digital(
    *,
    numero_identificacion: str,
    fecha_expedicion,
    primer_apellido: str,
    celular: str,
    correo: str,
) -> dict:
    if not _oracle_enabled():
        return {
            "ok": True,
            "message": "Validacion Exitosa",
            "raw": "Validacion Exitosa",
            "mocked": True,
        }

    fecha_formateada = _format_document_issue_date(fecha_expedicion)
    try:
        with closing(_connect()) as connection, closing(connection.cursor()) as cursor:
            raw_message = ""
            raw_payload = None
            refcursor_error = None

            try:
                with closing(connection.cursor()) as output_cursor:
                    if settings.DEBUG:
                        print(
                            "[XCORE][SP_CREDITODIGITAL] Consultando con OUT SYS_REFCURSOR "
                            f"numero_identificacion={numero_identificacion}"
                        )
                    cursor.callproc(
                        "SP_CREDITODIGITAL",
                        [
                            numero_identificacion,
                            fecha_formateada,
                            primer_apellido,
                            celular,
                            correo,
                            output_cursor,
                        ],
                    )
                    raw_message, raw_payload = _extract_credito_digital_cursor_message(output_cursor)
                    print("Trama", raw_message)
            except Exception as exc:
                refcursor_error = exc

            if not raw_message:
                result_var = cursor.var(str)
                try:
                    cursor.execute(
                        """
                        BEGIN
                            :resultado := SP_CREDITODIGITAL(
                                :cedula,
                                :fecha_expedicion,
                                :primer_apellido,
                                :celular,
                                :correo
                            );
                        END;
                        """,
                        resultado=result_var,
                        cedula=numero_identificacion,
                        fecha_expedicion=fecha_formateada,
                        primer_apellido=primer_apellido,
                        celular=celular,
                        correo=correo,
                    )
                except Exception:
                    cursor.callproc(
                        "SP_CREDITODIGITAL",
                        [
                            numero_identificacion,
                            fecha_formateada,
                            primer_apellido,
                            celular,
                            correo,
                            result_var,
                        ],
                    )
                raw_message = _normalize_credito_digital_message(result_var.getvalue())
                raw_payload = raw_message

            if not raw_message and refcursor_error is not None:
                raise refcursor_error
    except Exception as exc:
        raise OracleIntegrationError(str(exc)) from exc

    message = raw_message or "Sin respuesta del procedimiento SP_CREDITODIGITAL."
    return {
        "ok": _credito_digital_is_ok(message),
        "message": message,
        "raw": raw_payload if raw_payload not in (None, "") else raw_message,
    }


def consultar_capa(solicitud, numero_identificacion: str) -> dict:
    payload = {"numero_identificacion": numero_identificacion}
    consulta = None
    if solicitud is not None:
        consulta = ConsultaCoreOracle.objects.create(
            solicitud=solicitud,
            request_payload=payload,
            estado="PENDIENTE",
        )
    if not _oracle_enabled():
        data = {
            "estrato": "",
            "nivel_estudios": "",
            "estado_civil": "",
            "genero": "",
            "tipo_vivienda": "",
            "forma_pago": "",
            "tipo_contrato": "",
            "numero_personas_cargo": "",
            "edad": "",
            "antiguedad_asociado": "",
            "ingresos": "",
            "aportes_sociales": "",
            "activos": "",
            "pasivos": "",
            "valor_pasivos": 0,
            "saldo_creditos": 0,
            "ocupacion": "",
            "zona": "",
            "nombre": "",
            "mocked": True,
        }
        if consulta is not None:
            consulta.estado = "MOCK"
            consulta.response_payload = data
            consulta.mensaje = "Oracle deshabilitado. Respuesta mock."
            consulta.save(update_fields=("estado", "response_payload", "mensaje", "updated_at"))
        return data

    try:
        with closing(_connect()) as connection, closing(connection.cursor()) as cursor, closing(connection.cursor()) as output_cursor:
            if settings.DEBUG:
                print(f"[XCORE][{CAPACIDAD_PROCEDURE_NAME}] Consultando numero_identificacion={numero_identificacion}")
            cursor.callproc(CAPACIDAD_PROCEDURE_NAME, [numero_identificacion, output_cursor])
            row = output_cursor.fetchone()
            if not row:
                raise OracleIntegrationError(f"{CAPACIDAD_PROCEDURE_NAME} no devolvio datos.")
            data = _map_capacidad_row(row)
            if consulta is not None:
                consulta.estado = "OK"
                consulta.response_payload = data
                consulta.mensaje = f"Consulta Oracle exitosa ({CAPACIDAD_PROCEDURE_NAME})."
                consulta.save(update_fields=("estado", "response_payload", "mensaje", "updated_at"))
            return data
    except Exception as exc:
        if settings.DEBUG:
            print(f"[XCORE][{CAPACIDAD_PROCEDURE_NAME}] ERROR {type(exc).__name__}: {exc}")
        logger.error(
            "oracle.core_consult_failed procedure=%s numero_identificacion=%s error_type=%s error=%s",
            CAPACIDAD_PROCEDURE_NAME,
            numero_identificacion,
            type(exc).__name__,
            exc,
        )
        if consulta is not None:
            consulta.estado = "ERROR"
            consulta.response_payload = {"error": str(exc), "error_type": type(exc).__name__}
            consulta.mensaje = str(exc)
            consulta.save(update_fields=("estado", "response_payload", "mensaje", "updated_at"))
        raise

def consultar_familiar(solicitud, cedula: str) -> dict:
    payload = {"cedula": cedula}
    consulta = ConsultaEstamentoOracle.objects.create(
        solicitud=solicitud,
        request_payload=payload,
        estado="PENDIENTE",
    )
    if not _oracle_enabled():
        data = {"resultado": "NO", "tipofamiliar": "", "mocked": True}
        consulta.estado = "MOCK"
        consulta.resultado = "NO"
        consulta.tipo_familiar = ""
        consulta.response_payload = data
        consulta.mensaje = "Oracle deshabilitado. Respuesta mock."
        consulta.save(update_fields=("estado", "resultado", "tipo_familiar", "response_payload", "mensaje", "updated_at"))
        return data
    try:
        with closing(_connect()) as connection, closing(connection.cursor()) as cursor, closing(connection.cursor()) as output_cursor:
            cursor.callproc("SP_CONSULTAFAMILIAR", [cedula, output_cursor])
            result = {"resultado": "NO", "tipofamiliar": ""}
            for row in output_cursor:
                if row and len(row) > 1:
                    result["resultado"] = row[0]
                    result["tipofamiliar"] = row[1] or ""
                    break
            consulta.estado = "OK"
            consulta.resultado = result["resultado"]
            consulta.tipo_familiar = result["tipofamiliar"]
            consulta.response_payload = result
            consulta.mensaje = "Consulta familiar exitosa."
            consulta.save(update_fields=("estado", "resultado", "tipo_familiar", "response_payload", "mensaje", "updated_at"))
            return result
    except Exception as exc:
        consulta.estado = "ERROR"
        consulta.response_payload = {"error": str(exc)}
        consulta.mensaje = str(exc)
        consulta.save(update_fields=("estado", "response_payload", "mensaje", "updated_at"))
        raise


