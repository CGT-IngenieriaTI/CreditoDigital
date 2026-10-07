"""Ejemplos de wire sinteticos, no equivalencias ni catalogos del consumidor."""

from copy import deepcopy


RID = "00000000-0000-4000-8000-000000000001"
CID = "00000000-0000-4000-8000-000000000002"
FUENTE = "DOCUMENTADO_PROVEEDOR"


def codigo(original="DESCONOCIDO"):
    return dict(codigo_original=original, codigo_catalogo=None, descripcion=None, nombre=None,
                comportamiento=None, clasificacion=None, semantica="NO_IDENTIFICADO",
                fuente=None, referencia=None)


def nodo(nombre="Valor"):
    return dict(nombre=nombre, atributos=[["datoSintetico", "x"]], texto=None, hijos=[])


def importe(original="1.234567890123456789", normalizado="1.234567890123456789", *, moneda="1"):
    return dict(valor_original=original, unidad_original="COP" if normalizado is not None else None, moneda_codigo=moneda,
        moneda_homologada="COP" if normalizado is not None else None,
        valor_monetario=normalizado, disponible=original not in (None, "-1"),
        estado_conversion="CONVERTIDO_MONEDA_LEGAL" if normalizado is not None else "SIN_CONVERSION",
        motivo=None if normalizado is not None else "EVIDENCIA_SINTETICA_NO_CONVERTIDA",
        fuente="ADAPTACION_CONGENTE", referencia="fixture correccion 9F-B sintetica")


def valor(posicion=0):
    return dict(posicion_original=posicion, fecha="2026-10-01", moneda=codigo("1"),
        periodicidad=codigo("DESCONOCIDA"), cuota=importe(), saldoActual=importe("0", "0"),
        saldoMora=None, valorInicial=None, cupoTotal=None, datos_originales=nodo(), advertencias=[])


def obligacion(tipo="CuentaCartera", posicion=0):
    tipo_cuenta = dict(codigo_original="ZZ", subtipo_original="XX", codigo_catalogo=None,
        subcodigo_catalogo=None, abreviatura=None, descripcion=None, semantica="NO_IDENTIFICADO",
        fuente=None, referencia=None, alternativas=[])
    return dict(posicion_original=posicion, tipo_estructural=tipo, nombre_estructural_original=tipo,
        identificador={"numero": f"SINTETICA-{posicion}", "identificacion": None}, entidad="ENTIDAD FICTICIA",
        codSuscriptor=None, tipoCuenta=tipo_cuenta, tipoObligacion=None, sector=codigo(),
        calidadDeudor=None, garantia=None, estadoPago=codigo("XX"), estadoCuenta=None,
        estadoOrigen=None, estadoPlastico=None, formaPago=None, bloqueada_original="true",
        fechas={"apertura": None, "vencimiento": None}, valores=[valor()],
        valor_actual={"disponible": False, "motivo": "TEMPORALIDAD_NO_CERTIFICADA",
                      "posicion_original": None, "fuente": "ADAPTACION_CONGENTE"},
        estado_obligacion_compuesto={"estado": "NO_IDENTIFICADO", "nombre": None,
            "motivo": "EVIDENCIA_SINTETICA", "entradas_originales": [["EstadoPago", "XX"]],
            "fuente": "ADAPTACION_CONGENTE", "referencia": "fixture"},
        datos_originales=nodo(tipo), advertencias=[{"codigo": "SINTETICA", "campo": "sector", "fuente": FUENTE}])


def hdc():
    a = obligacion()
    b = obligacion("TarjetaCredito", 1)
    b["valores"] = [valor(0), valor(1), valor(2)]
    b["valores"][0]["cuota"] = importe("0", "0")
    b["valores"][1]["cuota"] = importe("-1", None)
    b["valores"][2]["cuota"] = importe("25", None, moneda="2")
    return {"requestId": RID, "versionContrato": "HDC_NORMALIZADO_V1", "resultado": {
        "consulta_id": CID, "request_id": RID, "fecha_consulta": "2026-10-01T12:00:00",
        "tipo_documento": codigo("1"), "tipo_documento_solicitado": "1", "estado_tecnico": "COMPLETADO",
        "codigo_respuesta_proveedor": "13", "atributos_informe_originales": [["fecha", "2026-10-01T12:00:00"]],
        "obligaciones": [a, b], "cobertura": {"total_obligaciones": 2, "total_carteras": 1,
            "total_tarjetas": 1, "total_ahorros": 0, "total_corrientes": 0, "total_otros": 0,
            "faltantes": ["embargo"], "advertencias": []},
        "trazabilidad": {"version_contrato": "HDC_NORMALIZADO_V1", "version_catalogos": "CATALOGO_SINTETICO",
                        "fuente": ["DOCUMENTO_SINTETICO"]}}}


def desglose(calidad="Principal", cuota="1936", pesos="1936000"):
    return dict(codigoTipo=None, tipo=None, calidadDeudor=calidad,
        cupo_original=None, cupo_pesos=None, saldo_original="3000", saldo_pesos="3000000",
        saldo_mora_original="0", saldo_mora_pesos="0", cuota_original=cuota, cuota_pesos=pesos,
        estados_importes=[["cupo", "AUSENTE"], ["saldo", "VALIDO"],
                          ["saldoMora", "VALIDO"], ["cuota", "VALIDO"]])


def endeudamiento():
    tipo = desglose()
    tipo.update(codigoTipo="SINTETICO", tipo="Producto sintetico")
    return dict(disponible=True, cuota_mensual_original="1936", cuota_mensual_pesos="1936000",
        cuota_control_original="1936.0", saldo_total_original="3000", saldo_total_pesos="3000000",
        saldo_mora_original="0", saldo_mora_pesos="0", calidad_deudor="Principal",
        unidad_original="MILES_COP", moneda_homologada="COP", fuente="ADAPTACION_CONGENTE",
        referencia="EVIDENCIA-SINTETICA-9F-B", estado_consistencia="CONSISTENTE",
        desglose_tipo_cuenta=[tipo], totales=[desglose(), desglose("Codeudor", "10", "10000"),
                                           desglose("Otros", "5", "5000")], advertencias=[])


def hdc_agregado():
    payload = hdc()
    payload["resultado"]["endeudamiento_actual"] = endeudamiento()
    payload["resultado"]["obligaciones"][0]["valores"][0]["cuota"] = importe("562000.0", "562000.0")
    return payload


def campo(codigo, valor, homologado=None):
    return {"codigo_original": codigo, "valor_original": valor, "valor_homologado": homologado,
            "fuente": FUENTE, "referencia": "manual sintetico"}


def preselecta(decision="APROBADO", estado="COMPLETADO"):
    decision_campo = None if decision is None else campo("DECISION", decision)
    return {"requestId": RID, "versionContrato": "PRESELECTA_NORMALIZADO_V1", "resultado": {
        "consulta_id": CID, "request_id": RID, "estado_tecnico": estado, "codigo_proveedor": "13",
        "codigo_proveedor_original": "13", "http_status": 200,
        "resultado_proveedor": {"decision": decision_campo, "riesgo": campo("RIESGO_SCORE", "SINTETICO"),
            "score": campo("SCORE", "701.000000000000000001", "701.000000000000000001"),
            "campos_motor": [] if decision_campo is None else [deepcopy(decision_campo)],
            "datos_documentados": {"nulo": None, "entero": 0, "booleano": True,
                                   "lista": ["sintetico", {"campo": 1}]}, "naturaleza": "RESULTADO_PROVEEDOR"},
        "cobertura": {"campos_presentes": ["DECISION", "RIESGO_SCORE", "SCORE"], "campos_ausentes": [], "advertencias": []},
        "trazabilidad": {"fecha_consulta": None, "fecha_registro": "2026-10-01T12:00:00-05:00",
            "consumidor": "health-test", "origen": "SINTETICO", "reutilizada": False,
            "version_contrato": "PRESELECTA_NORMALIZADO_V1", "fuente": "DOCUMENTO_SINTETICO"}, "error": None}}


def request_preselecta():
    return {"identidad": {"tipo_documento": "1", "numero_documento": "0000000000", "primer_apellido": "SINTETICO"},
        "estrategia_id": "25674", "parametros": {"LINEA_CREDITO": "1", "TIPO_ASOCIADO": "1", "MEDIO_PAGO": "1", "ACTIVIDAD": "1"}}


def request_hdc():
    return {"persona": "natural", "person_id_type": "1", "person_id_number": "0000000000", "person_last_name": "SINTETICO"}
