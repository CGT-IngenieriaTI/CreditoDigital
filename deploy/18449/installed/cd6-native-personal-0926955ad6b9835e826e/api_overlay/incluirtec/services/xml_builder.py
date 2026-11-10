# incluirtec/services/xml_builder.py
from xml.sax.saxutils import escape
from xml.etree import ElementTree as ET

from incluirtec.services.credit_validators import normalize_microcredit_codebtors
from incluirtec.services.xml_templates import (
    ESTRUCTURA_CREDITO_WORKFLOW,
    ESTRUCTURA_MICROCREDITO,
    ESTRUCTURA_RADICACION_CREDITO_DOCUMENTADA,
)


CREDIT_PRODUCT_LABELS = {
    "300": "Libre Inversion",
    "350": "Estudiantil",
    "360": "Rotativo",
    "380": "Cesion de CDAT",
    "390": "Credihoy",
}

CREDIT_MODEL_PRODUCT_CODES = {
    "SOLICITUD_DE_CREDITOS": "300",
    "SOLICITUD_LIBRE_INVERSION": "300",
    "SOLICITUD_CREDITO_ROTATIVO": "360",
    "SOLICITUD_CESION_CDAT": "380",
}

CREDIT_MODALITY_LABELS = {
    "102": "Planes VIS",
    "112": "Libre Inversion",
    "113": "Libre Inversion Condiciones Especiales",
    "118": "Seguros y Convenios",
    "120": "Cesion de CDATS 80 porciento",
    "125": "Estudiantil",
    "126": "Libre inversion - empleados informales",
    "127": "Libre Inversion - Credito Digital",
    "153": "Solicitud Cupo rotativo Virtual",
    "251": "Cupo rotativo Empresarial Persona Natural",
    "111": "Credihoy",
}

CREDIT_DESTINATION_LABELS = {
    "CFA": "Credihoy",
    "CGH": "Liberacion Gravamen Hipotecario",
    "PSC": "Campana Pre-aprobados sin credito CPSC-0226",
    "390": "Credihoy",
}


def _xml_value(value, default=""):
    if value is None:
        value = default
    elif value == "":
        value = default
    return escape(str(value))


def _build_nombre_completo(cliente):
    partes = [
        cliente.get("primerApellido", ""),
        cliente.get("segundoApellido", ""),
        cliente.get("primerNombre", ""),
        cliente.get("segundoNombre", ""),
    ]
    return " ".join(parte for parte in partes if parte).strip()


def _build_contactos_xml(data, cliente):
    contactos_xml = "<R_Contacto>\n"
    contactos = data.get("contactos", [])

    if not contactos:
        contactos_xml += (
            "<R_RegistroContacto "
            'A_TIPO_DIRECCION="C" '
            'A_DIRECCION_CONTACTO="CALLE 1 # 1-1" '
            'A_TELEFONO_CONTACTO="1234567" '
            f'A_MOVIL_CONTACTO="{_xml_value(cliente.get("numeroCelular"), "3001234567")}" '
            f'A_EMAIL_CONTACTO="{_xml_value(cliente.get("email"), "default@mail.com")}" '
            'A_CIUDAD_DIR="11001" '
            'A_BARRIO="CENTRO" '
            'A_CODIGO_DEPART="11" '
            'A_CODIGO_PAIS="169"></R_RegistroContacto>\n'
        )
    else:
        for contacto in contactos:
            contactos_xml += (
                "<R_RegistroContacto "
                f'A_TIPO_DIRECCION="{_xml_value(contacto.get("tipoDireccion"), "C")}" '
                f'A_DIRECCION_CONTACTO="{_xml_value(contacto.get("direccion"), "CALLE 1")}" '
                f'A_TELEFONO_CONTACTO="{_xml_value(contacto.get("telefono"), "1234567")}" '
                f'A_MOVIL_CONTACTO="{_xml_value(contacto.get("movil"), "3001234567")}" '
                f'A_EMAIL_CONTACTO="{_xml_value(contacto.get("email"), "default@mail.com")}" '
                f'A_CIUDAD_DIR="{_xml_value(contacto.get("ciudad"), "11001")}" '
                f'A_BARRIO="{_xml_value(contacto.get("barrio"), "CENTRO")}" '
                f'A_CODIGO_DEPART="{_xml_value(contacto.get("departamento"), "11")}" '
                f'A_CODIGO_PAIS="{_xml_value(contacto.get("pais"), "169")}"></R_RegistroContacto>\n'
            )

    contactos_xml += "</R_Contacto>"
    return contactos_xml


def _build_socioeconomica_xml(cliente):
    return f"""<R_Socioeconomica>
<R_RegistroSocioeconomica A_NIVEL_ESTUDIO="{_xml_value(cliente.get('nivelEstudio'), 'S')}" A_TIPO_VIVIENDA="{_xml_value(cliente.get('tipoVivienda'), 'P')}" A_ESTRATO="{_xml_value(cliente.get('estrato'), '3')}" A_ACTIVIDAD_ECONOMICA="{_xml_value(cliente.get('actividadEconomica'), 'EM')}" A_OCUPACION="1" A_POBVULNERABLE="N" A_NUM_PERCARGO="0" A_PUBEXP="N" A_ACTIVIDAD_CIIU="0122" A_ACTIVIDAD_CIIU_SECU="0121"></R_RegistroSocioeconomica>
</R_Socioeconomica>"""


def _build_financiera_xml():
    return """<R_Financiera>
<R_RegistroFinanciera A_OPERACIONES_MONEXT="N" A_DECLARA_RENTA="N" A_ADMINTRA_RECURSOS="N"></R_RegistroFinanciera>
</R_Financiera>"""


def _build_laboral_xml(data):
    laboral = data.get("laboral", {})
    medio_pago = laboral.get("medioPago") or "999"
    return f"""<R_Laboral>
<R_RegistroLaboral A_TIPO_CONTRATO="{_xml_value(laboral.get('tipoContrato'), 'TI')}" A_SALARIO_INTEGRAL="{_xml_value(laboral.get('salarioIntegral'), 'N')}" A_CARGO="99901" A_DEPENDENCIA="99901" A_JORNADA_LABORAL="{_xml_value(laboral.get('jornadaLaboral'), '1')}" A_MEDIO_PAGO="{_xml_value(medio_pago)}"></R_RegistroLaboral>
</R_Laboral>"""


def _resolve_credit_product_code(modelo_aplicado, credito):
    return str(
        credito.get("producto")
        or credito.get("codigoProducto")
        or CREDIT_MODEL_PRODUCT_CODES.get(modelo_aplicado, "300")
    ).strip().upper()


def _resolve_credit_product_label(producto):
    return CREDIT_PRODUCT_LABELS.get(producto, producto)


def _resolve_credit_modality_label(modalidad):
    return CREDIT_MODALITY_LABELS.get(modalidad, modalidad)


def _resolve_credit_destination_label(destinacion):
    return CREDIT_DESTINATION_LABELS.get(destinacion, destinacion)


def _empty_multiregister(tag_name):
    return f"<{tag_name}></{tag_name}>"


def build_credit_radication_xml(payload, line_config):
    cliente = payload["cliente"]
    solicitud = payload["solicitud"]
    contexto = payload["contexto"]

    datos_xml = f"""
<V_CODIGOINTERNO>{_xml_value(contexto.get('codigoInterno'))}</V_CODIGOINTERNO>
<A_CODIGO_CLIENTE>{_xml_value(cliente.get('identificacion'))}</A_CODIGO_CLIENTE>
<A_MODALIDAD_CREDITO>{_xml_value(solicitud.get('modalidadCredito'))}</A_MODALIDAD_CREDITO>
<A_OBSERVACION>{_xml_value(solicitud.get('observacion'))}</A_OBSERVACION>
<A_PAGARE>{_xml_value(solicitud.get('pagare'))}</A_PAGARE>
<A_FACOTRCON>{_xml_value(solicitud.get('factorContribucion'))}</A_FACOTRCON>
<V_MONTOSOLICITADO>{_xml_value(solicitud.get('montoSolicitado'))}</V_MONTOSOLICITADO>
<V_MONTO_MAX>{_xml_value(solicitud.get('montoMaximo'))}</V_MONTO_MAX>
<A_PERIODO_PAGO_CAPITAL>{_xml_value(solicitud.get('periodoPagoCapital'))}</A_PERIODO_PAGO_CAPITAL>
<A_PERIODO_PAGO_INTERES>{_xml_value(solicitud.get('periodoPagoInteres'))}</A_PERIODO_PAGO_INTERES>
<A_MEDIO_PAGO>{_xml_value(solicitud.get('medioPago'))}</A_MEDIO_PAGO>
<F_FECHA_SOLICITUD>{_xml_value(solicitud.get('fechaSolicitud'))}</F_FECHA_SOLICITUD>
<A_FUNCIONALIDAD>{_xml_value(contexto.get('funcionalidad'), line_config.workflow_application_code)}</A_FUNCIONALIDAD>
<V_NUMERO_CUOTAS>{_xml_value(solicitud.get('numeroCuotas'))}</V_NUMERO_CUOTAS>
<V_VALOR_CUOTA>{_xml_value(solicitud.get('valorCuota'))}</V_VALOR_CUOTA>
<V_TASA_NOMINAL_CREA>{_xml_value(solicitud.get('tasaNominalCrea'))}</V_TASA_NOMINAL_CREA>
<V_MONTO_GIR>{_xml_value(solicitud.get('montoGiro'))}</V_MONTO_GIR>
<A_DESTINACION>{_xml_value(solicitud.get('destinacion'))}</A_DESTINACION>
<A_NUMERO_CELULAR>{_xml_value(cliente.get('numeroCelular'))}</A_NUMERO_CELULAR>
<A_EMAIL>{_xml_value(cliente.get('email'))}</A_EMAIL>
<V_PROMOTOR>{_xml_value(solicitud.get('promotor'))}</V_PROMOTOR>
<A_SUCURSAL>{_xml_value(solicitud.get('sucursal'))}</A_SUCURSAL>"""

    formas_desembolso_xml = build_credit_disbursement_xml(payload["formasDesembolso"])
    obligaciones_xml = build_credit_pickups_xml(payload["obligacionesRecogidas"])
    codeudores_xml = build_credit_codebtors_xml(payload["codeudores"])

    return ESTRUCTURA_RADICACION_CREDITO_DOCUMENTADA.format(
        DATOS=datos_xml,
        OBLIGACIONES_RECOGIDAS=obligaciones_xml,
        CODEUDORES=codeudores_xml,
        FORMAS_DESEMBOLSO=formas_desembolso_xml,
    )


def build_credit_disbursement_xml(forms):
    if not forms:
        return _empty_multiregister("R_FormaDesembolsoCredito")

    xml = ["<R_FormaDesembolsoCredito>"]
    for form in forms:
        xml.append(
            "<R_RegistroFormaDesembolsoCredito "
            f'A_TIPO_LIQUIDACION="{_xml_value(form.get("tipoLiquidacion"))}" '
            f'A_CODIGO_CONT_CREDITO="{_xml_value(form.get("codigoContCredito"))}" '
            f'A_TIPO_MOVIMIENTO="{_xml_value(form.get("tipoMovimiento"))}" '
            f'A_NUMERO_CHEQUE="{_xml_value(form.get("numeroCheque"))}" '
            f'A_CODIGO_TERCERO="{_xml_value(form.get("codigoTercero"))}" '
            f'A_SUCURSAL="{_xml_value(form.get("sucursal"))}"></R_RegistroFormaDesembolsoCredito>'
        )
    xml.append("</R_FormaDesembolsoCredito>")
    return "\n".join(xml)


def build_credit_pickups_xml(pickups):
    if not pickups:
        return _empty_multiregister("R_ObligacionRecogida")

    xml = ["<R_ObligacionRecogida>"]
    for pickup in pickups:
        xml.append(
            "<R_RegistroObligacionRecogida "
            f'A_TIPO_OBLIGACION="{_xml_value(pickup.get("tipoObligacion"))}" '
            f'A_NUMERO_OBLIGACION_CARTERA="{_xml_value(pickup.get("numeroObligacionCartera"))}" '
            f'V_PARCIAL_RECOGIDO="{_xml_value(pickup.get("parcialRecogido"))}" '
            f'V_MONTO_LIQ="{_xml_value(pickup.get("montoLiq"))}" '
            f'A_ESTA_EN_CUENTA_COBRO="{_xml_value(pickup.get("estaEnCuentaCobro"), "N")}" '
            f'A_OBLIGATORIA="{_xml_value(pickup.get("obligatoria"), "N")}" '
            f'A_TIPO_RECOGIDA="{_xml_value(pickup.get("tipoRecogida"))}"></R_RegistroObligacionRecogida>'
        )
    xml.append("</R_ObligacionRecogida>")
    return "\n".join(xml)


def build_credit_codebtors_xml(codebtors):
    if not codebtors:
        return _empty_multiregister("R_Codeudor")

    xml = ["<R_Codeudor>"]
    for codebtor in codebtors:
        xml.append(
            "<R_RegistroCodeudor "
            f'A_TIPO_IDENTIFICACION="{_xml_value(codebtor.get("tipoIdentificacion"))}" '
            f'A_TIPO_IDENTIFICACION_D="{_xml_value(codebtor.get("tipoIdentificacionDescripcion"))}" '
            f'A_CODIGO_CLIENTE="{_xml_value(codebtor.get("identificacion"))}" '
            f'A_PRIMER_APELLIDO="{_xml_value(codebtor.get("primerApellido"))}" '
            f'A_SEGUNDO_APELLIDO="{_xml_value(codebtor.get("segundoApellido"))}" '
            f'A_PRIMER_NOMBRE="{_xml_value(codebtor.get("primerNombre"))}" '
            f'A_SEGUNDO_NOMBRE="{_xml_value(codebtor.get("segundoNombre"))}" '
            f'A_EMAIL="{_xml_value(codebtor.get("email"))}" '
            f'A_TELEFONO="{_xml_value(codebtor.get("telefono"))}" '
            f'F_NACIMIENTO="{_xml_value(codebtor.get("fechaNacimiento"))}" '
            f'A_GENERO="{_xml_value(codebtor.get("genero"))}" '
            f'A_DEUDOR_SOLIDARIO="{_xml_value(codebtor.get("deudorSolidario"), "Y")}" '
            f'A_PARENTESCO="{_xml_value(codebtor.get("parentesco"))}"></R_RegistroCodeudor>'
        )
    xml.append("</R_Codeudor>")
    return "\n".join(xml)


def _resolve_credihoy_credit(data):
    return data.get("credito") or data.get("solicitud") or {}


def _resolve_credihoy_customer(data):
    return data.get("cliente") or {}


def _resolve_credihoy_application(data, application=None):
    credito = _resolve_credihoy_credit(data)
    return {
        "tipo": (application or {}).get("K_TIPODR") or credito.get("tipoSolicitudCredito") or "SC",
        "numero": (application or {}).get("K_NUMDOC") or credito.get("numeroSolicitudCredito") or "",
    }


def _build_credihoy_common_fields(data, application=None):
    cliente = _resolve_credihoy_customer(data)
    credito = _resolve_credihoy_credit(data)
    app = _resolve_credihoy_application(data, application=application)
    codigo_interno = data.get("kIdTerc") or data.get("k_idterc") or credito.get("codigoInterno")
    producto = str(credito.get("producto") or credito.get("codigoProducto") or "390").strip().upper()
    modalidad = str(credito.get("modalidadCredito") or "111").strip().upper()
    destinacion = str(credito.get("destinacion") or "CFA").strip().upper()
    identificacion = cliente.get("identificacion") or data.get("nit")
    nombre = cliente.get("nombre") or _build_nombre_completo(cliente)

    return f"""
<A_SEGURO_VIDA>{_xml_value(credito.get('seguroVida'), 'P')}</A_SEGURO_VIDA>
<V_CODIGOINTERNO>{_xml_value(codigo_interno)}</V_CODIGOINTERNO>
<A_CODIGO_CLIENTE>{_xml_value(identificacion)}</A_CODIGO_CLIENTE>
<A_NOMBRE>{_xml_value(nombre)}</A_NOMBRE>
<A_MODALIDAD_CREDITO>{_xml_value(modalidad)}</A_MODALIDAD_CREDITO>
<A_MODALIDAD_CREDITO_D>{_xml_value(_resolve_credit_modality_label(modalidad))}</A_MODALIDAD_CREDITO_D>
<A_PRODUCTO>{_xml_value(producto)}</A_PRODUCTO>
<A_PRODUCTO_D>{_xml_value(_resolve_credit_product_label(producto))}</A_PRODUCTO_D>
<V_MONTOSOLICITADO>{_xml_value(credito.get('montoSolicitado'))}</V_MONTOSOLICITADO>
<V_NUMERO_CUENTA>{_xml_value(credito.get('numeroCuenta'))}</V_NUMERO_CUENTA>
<A_DEBITO_AUTO>{_xml_value(credito.get('debitoAutomatico'))}</A_DEBITO_AUTO>
<A_MODALCAPI_D>{_xml_value(credito.get('modalidadCapitalDescripcion'), 'No liquidar')}</A_MODALCAPI_D>
<V_PORCAPITA>{_xml_value(credito.get('porcentajeCapitalizacion'), '0')}</V_PORCAPITA>
<V_VALTOTCAP>{_xml_value(credito.get('valorTotalCapitalizacion'), '0')}</V_VALTOTCAP>
<A_MODALCAPI>{_xml_value(credito.get('modalidadCapital'), 'N')}</A_MODALCAPI>
<V_MONTO_MAX>{_xml_value(credito.get('montoMaximo') or credito.get('montoSolicitado'))}</V_MONTO_MAX>
<V_MONTO_CREDITO>{_xml_value(credito.get('montoCredito') or credito.get('montoSolicitado'))}</V_MONTO_CREDITO>
<A_PERIODO_PAGO_CAPITAL>{_xml_value(credito.get('periodoPagoCapital'), '1')}</A_PERIODO_PAGO_CAPITAL>
<A_PERIODO_PAGO_INTERES>{_xml_value(credito.get('periodoPagoInteres'), '1')}</A_PERIODO_PAGO_INTERES>
<A_AMORTIZACION>{_xml_value(credito.get('amortizacion'), 'CI')}</A_AMORTIZACION>
<A_AMORTIZACION_D>{_xml_value(credito.get('amortizacionDescripcion'), 'Cuota integral')}</A_AMORTIZACION_D>
<A_FORMA_APLICAR_TASA>{_xml_value(credito.get('formaAplicarTasa'), 'F')}</A_FORMA_APLICAR_TASA>
<A_FORMA_APLICAR_TASA_D>{_xml_value(credito.get('formaAplicarTasaDescripcion'), 'Fija')}</A_FORMA_APLICAR_TASA_D>
<A_MEDIO_PAGO>{_xml_value(credito.get('medioPago'), '999')}</A_MEDIO_PAGO>
<A_MEDIO_PAGO_D>{_xml_value(credito.get('medioPagoDescripcion'), 'VENTANILLA')}</A_MEDIO_PAGO_D>
<A_ACTIVIDAD_ECONOMICA>{_xml_value(cliente.get('actividadEconomica'), 'EM')}</A_ACTIVIDAD_ECONOMICA>
<A_ACTIVIDAD_ECONOMICA_D>{_xml_value(cliente.get('actividadEconomicaDescripcion'), 'Empleado')}</A_ACTIVIDAD_ECONOMICA_D>
<A_VALOR_RECOGIDO>{_xml_value(credito.get('valorRecogido'), '0')}</A_VALOR_RECOGIDO>
<A_CUMPLE_REGLA_NEGOCIO>{_xml_value(credito.get('cumpleReglaNegocio'))}</A_CUMPLE_REGLA_NEGOCIO>
<A_FUNCIONALIDAD>{_xml_value(credito.get('funcionalidad'), '19')}</A_FUNCIONALIDAD>
<V_NUMERO_CUOTAS>{_xml_value(credito.get('numeroCuotas'))}</V_NUMERO_CUOTAS>
<V_NUMERO_CUOTAS_CAL>{_xml_value(credito.get('numeroCuotasCal') or credito.get('numeroCuotas'))}</V_NUMERO_CUOTAS_CAL>
<V_VALOR_CUOTA_CAL>{_xml_value(credito.get('valorCuotaCal') or credito.get('valorCuota'))}</V_VALOR_CUOTA_CAL>
<V_VALOR_CUOTA>{_xml_value(credito.get('valorCuota'))}</V_VALOR_CUOTA>
<V_TASA_NOMINAL>{_xml_value(credito.get('tasaNominal'))}</V_TASA_NOMINAL>
<F_CUOTA_ORDINARIA>{_xml_value(credito.get('fechaCuotaOrdinaria'))}</F_CUOTA_ORDINARIA>
<V_MONTO_GIR>{_xml_value(credito.get('montoGiro') or credito.get('montoSolicitado'))}</V_MONTO_GIR>
<A_DESTINACION>{_xml_value(destinacion)}</A_DESTINACION>
<A_DESTINACION_D>{_xml_value(_resolve_credit_destination_label(destinacion))}</A_DESTINACION_D>
<A_COMENTARIO_PARA_ANALISTA>{_xml_value(credito.get('observacion'))}</A_COMENTARIO_PARA_ANALISTA>
<A_NUMERO_CELULAR>{_xml_value(cliente.get('numeroCelular'))}</A_NUMERO_CELULAR>
<A_EMAIL>{_xml_value(cliente.get('email'))}</A_EMAIL>
<A_CODIGO_PROMOTOR>{_xml_value(credito.get('promotor'))}</A_CODIGO_PROMOTOR>
<A_PROMOTOR>{_xml_value(credito.get('promotorNombre'))}</A_PROMOTOR>
<V_PROMOTOR>{_xml_value(credito.get('promotor'))}</V_PROMOTOR>
<F_FECHA_SOLICITUD>{_xml_value(credito.get('fechaSolicitud'))}</F_FECHA_SOLICITUD>
<A_SUCURSAL>{_xml_value(credito.get('sucursal') or data.get('sucursal'))}</A_SUCURSAL>
<NIT>{_xml_value(data.get('nit'))}</NIT>
<A_TIPO_SOLI_CREDITO>{_xml_value(app['tipo'])}</A_TIPO_SOLI_CREDITO>
<A_NUMERO_SOLI_CREDITO>{_xml_value(app['numero'])}</A_NUMERO_SOLI_CREDITO>
<A_DUMMY></A_DUMMY>"""


def build_credihoy_references_xml(references):
    def attribute(value, default=""):
        return _xml_value(value, default).replace('"', "&quot;").replace("'", "&apos;")

    if not references:
        return _empty_multiregister("R_REFERSOLCRED")

    xml = ["<R_REFERSOLCRED>"]
    for reference in references:
        xml.append(
            "<R_RegistroREFERSOLCRED "
            f'A_TIPO_REFERENCIA_ESE="{attribute(reference.get("tipoReferencia"))}" '
            f'A_REFERIDO="{attribute(reference.get("nombre") or reference.get("referido"))}" '
            f'A_DIRECCION_REFERENCIA_ESE="{attribute(reference.get("direccion"))}" '
            f'A_CIUDAD="{attribute(reference.get("ciudad"))}" '
            f'A_CIUDAD_D="{attribute(reference.get("ciudadDescripcion"))}" '
            f'A_CODIGO_CIUDAD="{attribute(reference.get("codigoCiudad") or reference.get("ciudad"))}" '
            f'A_CODIGO_DEPART="{attribute(reference.get("codigoDepartamento") or reference.get("departamento"))}" '
            f'A_CODIGO_PAIS="{attribute(reference.get("codigoPais") or reference.get("pais"), "169")}" '
            f'A_TELE_DOMICILIO_REF_ESE="{attribute(reference.get("telefono"))}" '
            f'A_EMAIL="{attribute(reference.get("email"))}" '
            f'A_OBSERVACION="{attribute(reference.get("observacion"))}" '
            f'A_PARENTESCO="{attribute(reference.get("parentesco"))}" '
            f'A_TIPO_REFERENCIA_ESE_D="{attribute(reference.get("tipoReferenciaDescripcion"))}"></R_RegistroREFERSOLCRED>'
        )
    xml.append("</R_REFERSOLCRED>")
    return "\n".join(xml)


def build_credihoy_capacity_xml(data, application=None):
    capacidad = data.get("capacidadPago") or {}
    devengos = capacidad.get("devengos") or []
    deducciones = capacidad.get("deducciones") or []

    return f"""<Entrada>
<Estructura>
<General>A_SEGURO_VIDA,V_CODIGOINTERNO,A_CODIGO_CLIENTE,A_NOMBRE,A_MODALIDAD_CREDITO,A_MODALIDAD_CREDITO_D,A_PRODUCTO,A_PRODUCTO_D,V_MONTOSOLICITADO,V_NUMERO_CUENTA,A_DEBITO_AUTO,A_MODALCAPI_D,V_PORCAPITA,V_VALTOTCAP,A_MODALCAPI,V_MONTO_MAX,V_MONTO_CREDITO,A_PERIODO_PAGO_CAPITAL,A_PERIODO_PAGO_INTERES,A_AMORTIZACION,A_AMORTIZACION_D,A_FORMA_APLICAR_TASA,A_FORMA_APLICAR_TASA_D,A_MEDIO_PAGO,A_MEDIO_PAGO_D,A_ACTIVIDAD_ECONOMICA,A_ACTIVIDAD_ECONOMICA_D,A_VALOR_RECOGIDO,A_CUMPLE_REGLA_NEGOCIO,A_FUNCIONALIDAD,V_NUMERO_CUOTAS,V_NUMERO_CUOTAS_CAL,V_VALOR_CUOTA_CAL,V_VALOR_CUOTA,V_TASA_NOMINAL,F_CUOTA_ORDINARIA,V_MONTO_GIR,A_DESTINACION,A_DESTINACION_D,A_COMENTARIO_PARA_ANALISTA,A_NUMERO_CELULAR,A_EMAIL,A_CODIGO_PROMOTOR,A_PROMOTOR,V_PROMOTOR,F_FECHA_SOLICITUD,A_SUCURSAL,NIT,A_TIPO_SOLI_CREDITO,A_NUMERO_SOLI_CREDITO,A_DUMMY,R_REFERSOLCRED[],R_FormularioDinamico[],R_CapacidadPagoDev[],R_CapacidadPagoDed[]</General>
<Individual>
<EstructuraR_REFERSOLCRED>R_RegistroREFERSOLCRED[]</EstructuraR_REFERSOLCRED>
<EstructuraR_RegistroREFERSOLCRED>A_TIPO_REFERENCIA_ESE,A_REFERIDO,A_DIRECCION_REFERENCIA_ESE,A_CIUDAD,A_CIUDAD_D,A_CODIGO_CIUDAD,A_CODIGO_DEPART,A_CODIGO_PAIS,A_TELE_DOMICILIO_REF_ESE,A_EMAIL,A_OBSERVACION,A_PARENTESCO,A_TIPO_REFERENCIA_ESE_D</EstructuraR_RegistroREFERSOLCRED>
<EstructuraR_FormularioDinamico>R_RegistroFormularioDinamico[]</EstructuraR_FormularioDinamico>
<EstructuraR_RegistroFormularioDinamico>A_FORMULARIO,A_FORMULARIO_D</EstructuraR_RegistroFormularioDinamico>
<EstructuraR_CapacidadPagoDev>R_RegistroCapacidadPagoDev[]</EstructuraR_CapacidadPagoDev>
<EstructuraR_RegistroCapacidadPagoDev>A_CONCEPTO_PAGO_CAP,A_VALOR_CONCEPTO_PAGO,A_VALOR_CONCEPTO_PAGO_EXT,A_CONCEPTO_PAGO_CAP_D,A_ACCION</EstructuraR_RegistroCapacidadPagoDev>
<EstructuraR_CapacidadPagoDed>R_RegistroCapacidadPagoDed[]</EstructuraR_CapacidadPagoDed>
<EstructuraR_RegistroCapacidadPagoDed>A_CONCEPTO_PAGO_CAP,A_VALOR_CONCEPTO_PAGO,A_VALOR_CONCEPTO_PAGO_EXT,A_CONCEPTO_PAGO_CAP_D,A_ACCION</EstructuraR_RegistroCapacidadPagoDed>
</Individual>
</Estructura>
<Datos>
{_build_credihoy_common_fields(data, application=application)}
{build_credihoy_references_xml(data.get('referencias') or [])}
<R_FormularioDinamico>
<R_RegistroFormularioDinamico A_FORMULARIO="10028" A_FORMULARIO_D="Formulario de Capacidad de pago para creditos"/>
</R_FormularioDinamico>
{_build_credihoy_capacity_register_xml('R_CapacidadPagoDev', 'R_RegistroCapacidadPagoDev', devengos)}
{_build_credihoy_capacity_register_xml('R_CapacidadPagoDed', 'R_RegistroCapacidadPagoDed', deducciones)}
</Datos>
<Responsable>
<v_codigointerno_tercero>{_xml_value(data.get('kIdTerc') or data.get('k_idterc'))}</v_codigointerno_tercero>
</Responsable>
</Entrada>"""


def _build_credihoy_capacity_register_xml(container, record_tag, records):
    xml = [f"<{container}>"]
    for record in records:
        xml.append(
            f"<{record_tag} "
            f'A_CONCEPTO_PAGO_CAP="{_xml_value(record.get("codigo"))}" '
            f'A_VALOR_CONCEPTO_PAGO="{_xml_value(record.get("valor"))}" '
            f'A_VALOR_CONCEPTO_PAGO_EXT="{_xml_value(record.get("valorExt"), "0")}" '
            f'A_CONCEPTO_PAGO_CAP_D="{_xml_value(record.get("descripcion"))}" '
            f'A_ACCION="{_xml_value(record.get("accion"), "U")}"/>'
        )
    xml.append(f"</{container}>")
    return "\n".join(xml)


def build_credihoy_central_risk_xml(data, application=None):
    central = data.get("centrales") or {}
    cliente = _resolve_credihoy_customer(data)
    app = _resolve_credihoy_application(data, application=application)
    codigo_interno = data.get("kIdTerc") or data.get("k_idterc")
    identificacion = cliente.get("identificacion") or data.get("nit")

    central_xml = (
        "<R_CENTRALRIESGODEUDOR>\n"
        "<R_RegistroCENTRALRIESGODEUDOR "
        f'A_CODIGO_CLIENTE="{_xml_value(identificacion)}" '
        f'A_RAZON_SOCIAL="{_xml_value(central.get("razonSocial"))}" '
        f'A_CENTRAL_RIESGO="{_xml_value(central.get("codigoCentral"), "200")}" '
        f'A_TIPO_REPOR_CENTRAL_RIESGO="{_xml_value(central.get("tipoReporte"))}" '
        f'F_REPOR_CENTRAL_RIESGO="{_xml_value(central.get("fechaReporte"))}" '
        f'A_CALIFI_CENTRAL_RIESGO="{_xml_value(central.get("score"))}" '
        f'A_OBSERV_CENTRAL_RIESGO="{_xml_value(central.get("observacion"))}" '
        f'A_PROB_INCUMPLIM_CENTRAL_RIESGO="{_xml_value(central.get("probabilidadIncumplimiento"))}" '
        f'A_PROB_MOR_CENTRAL_RIESGO="{_xml_value(central.get("probabilidadMora"))}" '
        f'A_CALIFICACION_CENTRAL_RIESGO="{_xml_value(central.get("calificacion"))}" '
        f'A_CALIFICACION_CENTRAL_RIESGO_D="{_xml_value(central.get("calificacionDescripcion"))}" '
        f'A_TIPO_SOLI_CREDITO="{_xml_value(app["tipo"])}" '
        f'A_NUMERO_SOLI_CREDITO="{_xml_value(app["numero"])}" '
        f'V_CODIGOINTERNO="{_xml_value(codigo_interno)}" '
        f'A_PRIMER_APELLIDO="{_xml_value(cliente.get("primerApellido"))}" '
        f'A_SEGUNDO_APELLIDO="{_xml_value(cliente.get("segundoApellido"))}" '
        f'A_PRIMER_NOMBRE="{_xml_value(cliente.get("primerNombre"))}" '
        f'A_SEGUNDO_NOMBRE="{_xml_value(cliente.get("segundoNombre"))}" '
        f'A_CENTRAL_RIESGO_D="{_xml_value(central.get("proveedor"), "DATACREDITO")}" '
        f'A_TIPO_REPOR_CENTRAL_RIESGO_D="{_xml_value(central.get("tipoReporteDescripcion"))}"/>'
        "\n</R_CENTRALRIESGODEUDOR>"
    )

    return f"""<Entrada>
<Estructura>
<General>A_SEGURO_VIDA,V_CODIGOINTERNO,A_CODIGO_CLIENTE,A_NOMBRE,A_MODALIDAD_CREDITO,A_MODALIDAD_CREDITO_D,A_PRODUCTO,A_PRODUCTO_D,V_MONTOSOLICITADO,V_NUMERO_CUENTA,A_DEBITO_AUTO,A_MODALCAPI_D,V_PORCAPITA,V_VALTOTCAP,A_MODALCAPI,V_MONTO_MAX,V_MONTO_CREDITO,A_PERIODO_PAGO_CAPITAL,A_PERIODO_PAGO_INTERES,A_AMORTIZACION,A_AMORTIZACION_D,A_FORMA_APLICAR_TASA,A_FORMA_APLICAR_TASA_D,A_MEDIO_PAGO,A_MEDIO_PAGO_D,A_ACTIVIDAD_ECONOMICA,A_ACTIVIDAD_ECONOMICA_D,A_VALOR_RECOGIDO,A_CUMPLE_REGLA_NEGOCIO,A_FUNCIONALIDAD,V_NUMERO_CUOTAS,V_NUMERO_CUOTAS_CAL,V_VALOR_CUOTA_CAL,V_VALOR_CUOTA,V_TASA_NOMINAL,F_CUOTA_ORDINARIA,V_MONTO_GIR,A_DESTINACION,A_DESTINACION_D,A_COMENTARIO_PARA_ANALISTA,A_NUMERO_CELULAR,A_EMAIL,A_CODIGO_PROMOTOR,A_PROMOTOR,V_PROMOTOR,F_FECHA_SOLICITUD,A_SUCURSAL,NIT,A_TIPO_SOLI_CREDITO,A_NUMERO_SOLI_CREDITO,A_DUMMY,R_REFERSOLCRED[],R_CapacidadPagoDev[],R_CapacidadPagoDed[],R_CENTRALRIESGODEUDOR[]</General>
<Individual>
<EstructuraR_CENTRALRIESGODEUDOR>R_RegistroCENTRALRIESGODEUDOR[]</EstructuraR_CENTRALRIESGODEUDOR>
<EstructuraR_RegistroCENTRALRIESGODEUDOR>A_CODIGO_CLIENTE,A_RAZON_SOCIAL,A_CENTRAL_RIESGO,A_TIPO_REPOR_CENTRAL_RIESGO,F_REPOR_CENTRAL_RIESGO,A_CALIFI_CENTRAL_RIESGO,A_OBSERV_CENTRAL_RIESGO,A_PROB_INCUMPLIM_CENTRAL_RIESGO,A_PROB_MOR_CENTRAL_RIESGO,A_CALIFICACION_CENTRAL_RIESGO,A_CALIFICACION_CENTRAL_RIESGO_D,A_TIPO_SOLI_CREDITO,A_NUMERO_SOLI_CREDITO,V_CODIGOINTERNO,A_PRIMER_APELLIDO,A_SEGUNDO_APELLIDO,A_PRIMER_NOMBRE,A_SEGUNDO_NOMBRE,A_CENTRAL_RIESGO_D,A_TIPO_REPOR_CENTRAL_RIESGO_D</EstructuraR_RegistroCENTRALRIESGODEUDOR>
</Individual>
</Estructura>
<Datos>
{_build_credihoy_common_fields(data, application=application)}
{build_credihoy_references_xml(data.get('referencias') or [])}
{_build_credihoy_capacity_register_xml('R_CapacidadPagoDev', 'R_RegistroCapacidadPagoDev', (data.get('capacidadPago') or {}).get('devengos') or [])}
{_build_credihoy_capacity_register_xml('R_CapacidadPagoDed', 'R_RegistroCapacidadPagoDed', (data.get('capacidadPago') or {}).get('deducciones') or [])}
{central_xml}
</Datos>
<Responsable>
<v_codigointerno_tercero>{_xml_value(codigo_interno)}</v_codigointerno_tercero>
</Responsable>
</Entrada>"""


def build_credihoy_digital_radication_xml(data, *, without_promoter_offline=False):
    if type(without_promoter_offline) is not bool:
        raise ValueError("OFFLINE_PROMOTOR_FLAG_INVALIDO")
    if without_promoter_offline and any(
            data.get("credito", {}).get(key) is not None for key in ("promotor", "promotorNombre")):
        raise ValueError("OFFLINE_PROMOTOR_NO_AUSENTE")
    xml = f"""<Entrada>
<Estructura>
<General>A_SEGURO_VIDA,V_CODIGOINTERNO,A_CODIGO_CLIENTE,A_NOMBRE,A_MODALIDAD_CREDITO,A_MODALIDAD_CREDITO_D,A_PRODUCTO,A_PRODUCTO_D,V_MONTOSOLICITADO,V_NUMERO_CUENTA,A_DEBITO_AUTO,A_MODALCAPI_D,V_PORCAPITA,V_VALTOTCAP,A_MODALCAPI,V_MONTO_MAX,V_MONTO_CREDITO,A_PERIODO_PAGO_CAPITAL,A_PERIODO_PAGO_INTERES,A_AMORTIZACION,A_AMORTIZACION_D,A_FORMA_APLICAR_TASA,A_FORMA_APLICAR_TASA_D,A_MEDIO_PAGO,A_MEDIO_PAGO_D,A_ACTIVIDAD_ECONOMICA,A_ACTIVIDAD_ECONOMICA_D,A_VALOR_RECOGIDO,A_CUMPLE_REGLA_NEGOCIO,A_FUNCIONALIDAD,V_NUMERO_CUOTAS,V_NUMERO_CUOTAS_CAL,V_VALOR_CUOTA_CAL,V_VALOR_CUOTA,V_TASA_NOMINAL,F_CUOTA_ORDINARIA,V_MONTO_GIR,A_DESTINACION,A_DESTINACION_D,A_COMENTARIO_PARA_ANALISTA,A_NUMERO_CELULAR,A_EMAIL,A_CODIGO_PROMOTOR,A_PROMOTOR,V_PROMOTOR,F_FECHA_SOLICITUD,A_SUCURSAL,NIT,A_TIPO_SOLI_CREDITO,A_NUMERO_SOLI_CREDITO,A_DUMMY,R_REFERSOLCRED[]</General>
<Individual>
<EstructuraR_REFERSOLCRED>R_RegistroREFERSOLCRED[]</EstructuraR_REFERSOLCRED>
<EstructuraR_RegistroREFERSOLCRED>A_TIPO_REFERENCIA_ESE,A_REFERIDO,A_DIRECCION_REFERENCIA_ESE,A_CIUDAD,A_CIUDAD_D,A_CODIGO_CIUDAD,A_CODIGO_DEPART,A_CODIGO_PAIS,A_TELE_DOMICILIO_REF_ESE,A_EMAIL,A_OBSERVACION,A_PARENTESCO,A_TIPO_REFERENCIA_ESE_D</EstructuraR_RegistroREFERSOLCRED>
</Individual>
</Estructura>
<Datos>
{_build_credihoy_common_fields(data)}
{build_credihoy_references_xml(data.get('referencias') or [])}
</Datos>
<Responsable>
<v_codigointerno_tercero>{_xml_value(data.get('kIdTerc') or data.get('k_idterc'))}</v_codigointerno_tercero>
</Responsable>
</Entrada>"""
    credit = _resolve_credihoy_credit(data)
    selectors = (
        ("tipoConsulta", "A_TIPO_CONSULTA", "2"),
        ("modalidadSimular", "A_MODALIDAD_SIMULAR", "M"),
        ("datoCalcularSimulacion", "A_DATO_CALCULAR_SIMULACION", "C"),
    )
    supplied = [key for key, _, _ in selectors if key in credit]
    if supplied and (len(supplied) != len(selectors)
                     or any(credit[key] != expected for key, _, expected in selectors)):
        raise ValueError("CREDIHOY_SELECTORES_113_NO_SOPORTADOS")
    if not without_promoter_offline and not supplied:
        return xml
    root = ET.fromstring(xml)
    general = root.find("./Estructura/General")
    if supplied:
        # Explicit caller values only; preserve the calculated and input quotas separately.
        for key, tag, _ in selectors:
            ET.SubElement(root.find("./Datos"), tag).text = credit[key]
        general.text += "," + ",".join(tag for _, tag, _ in selectors)
    if without_promoter_offline:
        # Candidate omission only; this does not certify activity 113 compatibility.
        tags = {"A_CODIGO_PROMOTOR", "A_PROMOTOR", "V_PROMOTOR"}
        general.text = ",".join(tag for tag in general.text.split(",") if tag not in tags)
        for node in list(root.find("./Datos")):
            if node.tag in tags:
                root.find("./Datos").remove(node)
    return ET.tostring(root, encoding="unicode")

def build_microcredito_legacy_xml(data):
    cliente = data.get('cliente', {})
    
    # ===== DATOS GENERALES (OBLIGATORIOS) =====
    datos_xml = f"""
<A_TIPO_DOC>{cliente.get('tipoDocumento', 'C')}</A_TIPO_DOC>
<A_CODIGO_CLIENTE>{cliente.get('identificacion')}</A_CODIGO_CLIENTE>
<A_PRIMER_APELLIDO>{cliente.get('primerApellido', '')}</A_PRIMER_APELLIDO>
<A_SEGUNDO_APELLIDO>{cliente.get('segundoApellido', '')}</A_SEGUNDO_APELLIDO>
<A_PRIMER_NOMBRE>{cliente.get('primerNombre', '')}</A_PRIMER_NOMBRE>
<A_SEGUNDO_NOMBRE>{cliente.get('segundoNombre', '')}</A_SEGUNDO_NOMBRE>
<F_NACIMIENTO>{cliente.get('fechaNacimiento', '01/01/1990')}</F_NACIMIENTO>
<A_ESTADO_CIVIL>{cliente.get('estadoCivil', 'S')}</A_ESTADO_CIVIL>
<A_GENERO>{cliente.get('genero', 'M')}</A_GENERO>
<A_NATURALEZA>N</A_NATURALEZA>
<A_ACTIVO>Y</A_ACTIVO>
<A_NOMBRE>{cliente.get('primerApellido', '')} {cliente.get('segundoApellido', '')} {cliente.get('primerNombre', '')} {cliente.get('segundoNombre', '')}</A_NOMBRE>
<A_ESTRATO>{cliente.get('estrato', '3')}</A_ESTRATO>
<A_NOMINA>999</A_NOMINA>
<A_SUCURSAL>{data.get('sucursal', '101')}</A_SUCURSAL>
<A_ASOCON>1</A_ASOCON>
<A_CENCOS>999</A_CENCOS>
<A_SECCION>100</A_SECCION>
<A_UBICACION_UNO>101</A_UBICACION_UNO>
<A_UBICACION_DOS>10101</A_UBICACION_DOS>
<A_TIPO_CUENTA>A</A_TIPO_CUENTA>
<A_ESTADO_CLIENTE>A</A_ESTADO_CLIENTE>
<A_CLIENTE_ASOCIADO>A</A_CLIENTE_ASOCIADO>
<A_TIPASO>5</A_TIPASO>
<A_ACTIVIDAD_ECONOMICA>{cliente.get('actividadEconomica', 'EM')}</A_ACTIVIDAD_ECONOMICA>
<V_SUELDO_ASOC>{cliente.get('salario', '0')}</V_SUELDO_ASOC>
<A_TELEFONO>1</A_TELEFONO>
<A_NUM_CELULAR>{cliente.get('numeroCelular', '')}</A_NUM_CELULAR>
<A_EMAIL>{cliente.get('email', '')}</A_EMAIL>
<A_REGIMEN_IVA>S</A_REGIMEN_IVA>
<A_TIPO_CON>D</A_TIPO_CON>
<A_AUTORETENEDOR>N</A_AUTORETENEDOR>
<A_REGIMEN_IMP_RENTA>T</A_REGIMEN_IMP_RENTA>
<A_FUNCIONALIDAD>6</A_FUNCIONALIDAD>
<A_CARGO>99901</A_CARGO>
<A_CODIGO_CARGO>99901</A_CODIGO_CARGO>
<A_DEPENDENCIA>99901</A_DEPENDENCIA>
<A_JORNADA_LABORAL>1</A_JORNADA_LABORAL>
<A_CODIGO_CONCEPTO>101</A_CODIGO_CONCEPTO>"""

    # ===== CONTACTOS =====
    contactos_xml = _build_contactos_xml(data, cliente)

    # ===== SOCIOECONOMICA =====
    socioeconomica_xml = _build_socioeconomica_xml(cliente)

    # ===== FINANCIERA =====
    financiera_xml = _build_financiera_xml()

    # ===== LABORAL =====
    laboral_xml = _build_laboral_xml(data)

    # ===== ACTIVOS (vacío) =====
    activos_xml = "<R_Activos></R_Activos>"

    # ===== PASIVOS (vacío) =====
    pasivos_xml = "<R_Pasivos></R_Pasivos>"

    # ===== CONSTRUIR XML FINAL =====
    xml = ESTRUCTURA_MICROCREDITO.format(
        DATOS=datos_xml,
        CONTACTOS=contactos_xml,
        SOCIOECONOMICA=socioeconomica_xml,
        FINANCIERA=financiera_xml,
        LABORAL=laboral_xml,
        ACTIVOS=activos_xml,
        PASIVOS=pasivos_xml
    )

    return xml


def build_microcredito_disbursement_xml(forms):
    if not forms:
        return _empty_multiregister("R_FormaDesembolsoCredito")

    xml = ["<R_FormaDesembolsoCredito>"]
    for form in forms:
        xml.append(
            "<R_RegistroFormaDesembolsoCredito "
            f'A_NUMERO_CHEQUE="{_xml_value(form.get("numeroCheque"))}" '
            f'A_CODIGO_CONT_CREDITO="{_xml_value(form.get("codigoContCredito"))}" '
            f'A_CODIGO_TERCERO="{_xml_value(form.get("codigoTercero"))}" '
            f'A_SUCURSAL="{_xml_value(form.get("sucursal"))}" '
            f'A_TIPO_LIQUIDACION="{_xml_value(form.get("tipoLiquidacion"))}" '
            f'A_TIPO_MOVIMIENTO="{_xml_value(form.get("tipoMovimiento"))}"></R_RegistroFormaDesembolsoCredito>'
        )
    xml.append("</R_FormaDesembolsoCredito>")
    return "\n".join(xml)


def build_microcredito_xml(data):
    cliente = data.get("cliente", {})
    credito = data.get("credito", {})
    formas_desembolso = data.get("formasDesembolso") or data.get("formas_desembolso") or []
    codeudores = normalize_microcredit_codebtors(data.get("codeudores"))
    codigo_interno = data.get("kIdTerc") or data.get("k_idterc") or credito.get("codigoInterno")

    datos_xml = f"""
<A_MODALIDAD_CREDITO>{_xml_value(credito.get('modalidadCredito'))}</A_MODALIDAD_CREDITO>
<A_DESTINACION>{_xml_value(credito.get('destinacion'))}</A_DESTINACION>
<V_NUMERO_CUOTAS>{_xml_value(credito.get('numeroCuotas'))}</V_NUMERO_CUOTAS>
<A_MODALCAPI>{_xml_value(credito.get('modalidadCapital'), 'V')}</A_MODALCAPI>
<A_PERIODO_PAGO_CAPITAL>{_xml_value(credito.get('periodoPagoCapital'), '1')}</A_PERIODO_PAGO_CAPITAL>
<A_CODIGO_CLIENTE>{_xml_value(cliente.get('identificacion') or data.get('nit'))}</A_CODIGO_CLIENTE>
<V_PORSEGVIDA>{_xml_value(credito.get('porcentajeSeguroVida') or credito.get('tasaVidaDeudores'), '0.090')}</V_PORSEGVIDA>
<A_PAGARE>{_xml_value(credito.get('pagare'), '0')}</A_PAGARE>
<V_TASA_NOMINAL_CREA>{_xml_value(credito.get('tasaNominalCrea') or credito.get('tasaNominal'))}</V_TASA_NOMINAL_CREA>
<A_MEDIO_PAGO>{_xml_value(credito.get('medioPago'), '999')}</A_MEDIO_PAGO>
<V_VALOR_CUOTA>{_xml_value(credito.get('valorCuota'))}</V_VALOR_CUOTA>
<V_MONTOSOLICITADO>{_xml_value(credito.get('montoSolicitado'))}</V_MONTOSOLICITADO>
<V_PORCAPITA>{_xml_value(credito.get('porcentajeCapitalizacion'))}</V_PORCAPITA>
<F_FECHA_SOLICITUD>{_xml_value(credito.get('fechaSolicitud'))}</F_FECHA_SOLICITUD>
<V_CODIGOINTERNO>{_xml_value(codigo_interno)}</V_CODIGOINTERNO>
<A_PERIODO_PAGO_INTERES>{_xml_value(credito.get('periodoPagoInteres'), '1')}</A_PERIODO_PAGO_INTERES>
<A_FACOTRCON>{_xml_value(credito.get('factorContribucion') or credito.get('factorComision'))}</A_FACOTRCON>
<A_OBSERVACION>{_xml_value(credito.get('observacion'))}</A_OBSERVACION>
<A_SUCURSAL>{_xml_value(credito.get('sucursal') or data.get('sucursal'))}</A_SUCURSAL>
<V_VALTOTCAP>{_xml_value(credito.get('valorTotalCapitalizacion'), '0')}</V_VALTOTCAP>"""

    return f"""<Entrada>
<Estructura>
<General>A_MODALIDAD_CREDITO,A_DESTINACION,V_NUMERO_CUOTAS,A_MODALCAPI,A_PERIODO_PAGO_CAPITAL,A_CODIGO_CLIENTE,V_PORSEGVIDA,A_PAGARE,V_TASA_NOMINAL_CREA,A_MEDIO_PAGO,V_VALOR_CUOTA,V_MONTOSOLICITADO,V_PORCAPITA,F_FECHA_SOLICITUD,V_CODIGOINTERNO,A_PERIODO_PAGO_INTERES,A_FACOTRCON,A_OBSERVACION,A_SUCURSAL,V_VALTOTCAP,R_FormaDesembolsoCredito[],R_ObligacionRecogida[],R_Codeudor[],A_DUMMY</General>
<Individual>
<EstructuraR_FormaDesembolsoCredito>R_RegistroFormaDesembolsoCredito[]</EstructuraR_FormaDesembolsoCredito>
<EstructuraR_RegistroFormaDesembolsoCredito>A_NUMERO_CHEQUE,A_CODIGO_CONT_CREDITO,A_CODIGO_TERCERO,A_SUCURSAL,A_TIPO_LIQUIDACION,A_TIPO_MOVIMIENTO</EstructuraR_RegistroFormaDesembolsoCredito>
<EstructuraR_ObligacionRecogida>R_RegistroObligacionRecogida[]</EstructuraR_ObligacionRecogida>
<EstructuraR_RegistroObligacionRecogida>A_TIPO_OBLIGACION,A_NUMERO_OBLIGACION_CARTERA,V_PARCIAL_RECOGIDO,V_MONTO_LIQ,A_ESTA_EN_CUENTA_COBRO,A_OBLIGATORIA,A_TIPO_RECOGIDA</EstructuraR_RegistroObligacionRecogida>
<EstructuraR_Codeudor>R_RegistroCodeudor[]</EstructuraR_Codeudor>
<EstructuraR_RegistroCodeudor>A_TIPO_IDENTIFICACION,A_TIPO_IDENTIFICACION_D,A_CODIGO_CLIENTE,A_PRIMER_APELLIDO,A_SEGUNDO_APELLIDO,A_PRIMER_NOMBRE,A_SEGUNDO_NOMBRE,A_EMAIL,A_TELEFONO,F_NACIMIENTO,A_GENERO,A_DEUDOR_SOLIDARIO,A_PARENTESCO</EstructuraR_RegistroCodeudor>
</Individual>
</Estructura>
<Datos>
{datos_xml}
{build_microcredito_disbursement_xml(formas_desembolso)}
{build_credit_pickups_xml([])}
{build_credit_codebtors_xml(codeudores)}
<A_DUMMY></A_DUMMY>
</Datos>
<Responsable>
<v_codigointerno_tercero>{_xml_value(codigo_interno)}</v_codigointerno_tercero>
</Responsable>
</Entrada>"""


def build_credit_workflow_xml(data, modelo_aplicado):
    cliente = data.get("cliente", {})
    credito = data.get("credito", {})
    producto = _resolve_credit_product_code(modelo_aplicado, credito)
    modalidad = str(credito.get("modalidadCredito", "")).strip().upper()
    destinacion = str(credito.get("destinacion", "")).strip().upper()
    nombre = _build_nombre_completo(cliente)

    datos_xml = f"""
<A_TIPO_DOC>{_xml_value(cliente.get('tipoDocumento'), 'C')}</A_TIPO_DOC>
<A_CODIGO_CLIENTE>{_xml_value(cliente.get('identificacion'))}</A_CODIGO_CLIENTE>
<A_PRIMER_APELLIDO>{_xml_value(cliente.get('primerApellido'))}</A_PRIMER_APELLIDO>
<A_SEGUNDO_APELLIDO>{_xml_value(cliente.get('segundoApellido'))}</A_SEGUNDO_APELLIDO>
<A_PRIMER_NOMBRE>{_xml_value(cliente.get('primerNombre'))}</A_PRIMER_NOMBRE>
<A_SEGUNDO_NOMBRE>{_xml_value(cliente.get('segundoNombre'))}</A_SEGUNDO_NOMBRE>
<F_NACIMIENTO>{_xml_value(cliente.get('fechaNacimiento'), '01/01/1990')}</F_NACIMIENTO>
<A_ESTADO_CIVIL>{_xml_value(cliente.get('estadoCivil'), 'S')}</A_ESTADO_CIVIL>
<A_GENERO>{_xml_value(cliente.get('genero'), 'M')}</A_GENERO>
<A_NATURALEZA>N</A_NATURALEZA>
<A_ACTIVO>Y</A_ACTIVO>
<A_NOMBRE>{_xml_value(nombre)}</A_NOMBRE>
<A_ESTRATO>{_xml_value(cliente.get('estrato'), '3')}</A_ESTRATO>
<A_NOMINA>999</A_NOMINA>
<A_SUCURSAL>{_xml_value(data.get('sucursal'), '101')}</A_SUCURSAL>
<A_ASOCON>1</A_ASOCON>
<A_CENCOS>999</A_CENCOS>
<A_SECCION>100</A_SECCION>
<A_UBICACION_UNO>101</A_UBICACION_UNO>
<A_UBICACION_DOS>10101</A_UBICACION_DOS>
<A_TIPO_CUENTA>A</A_TIPO_CUENTA>
<A_ESTADO_CLIENTE>A</A_ESTADO_CLIENTE>
<A_CLIENTE_ASOCIADO>A</A_CLIENTE_ASOCIADO>
<A_TIPASO>5</A_TIPASO>
<A_ACTIVIDAD_ECONOMICA>{_xml_value(cliente.get('actividadEconomica'), 'EM')}</A_ACTIVIDAD_ECONOMICA>
<V_SUELDO_ASOC>{_xml_value(cliente.get('salario'), '0')}</V_SUELDO_ASOC>
<A_TELEFONO>{_xml_value(cliente.get('telefono'), '1')}</A_TELEFONO>
<A_NUM_CELULAR>{_xml_value(cliente.get('numeroCelular'))}</A_NUM_CELULAR>
<A_EMAIL>{_xml_value(cliente.get('email'))}</A_EMAIL>
<A_REGIMEN_IVA>S</A_REGIMEN_IVA>
<A_TIPO_CON>D</A_TIPO_CON>
<A_AUTORETENEDOR>N</A_AUTORETENEDOR>
<A_REGIMEN_IMP_RENTA>T</A_REGIMEN_IMP_RENTA>
<A_FUNCIONALIDAD>6</A_FUNCIONALIDAD>
<A_CARGO>99901</A_CARGO>
<A_CODIGO_CARGO>99901</A_CODIGO_CARGO>
<A_DEPENDENCIA>99901</A_DEPENDENCIA>
<A_JORNADA_LABORAL>1</A_JORNADA_LABORAL>
<A_CODIGO_CONCEPTO>101</A_CODIGO_CONCEPTO>
<A_PRODUCTO>{_xml_value(producto)}</A_PRODUCTO>
<A_PRODUCTO_D>{_xml_value(_resolve_credit_product_label(producto))}</A_PRODUCTO_D>
<A_MODALIDAD_CREDITO>{_xml_value(modalidad)}</A_MODALIDAD_CREDITO>
<A_MODALIDAD_CREDITO_D>{_xml_value(_resolve_credit_modality_label(modalidad))}</A_MODALIDAD_CREDITO_D>
<A_DESTINACION>{_xml_value(destinacion)}</A_DESTINACION>
<A_DESTINACION_D>{_xml_value(_resolve_credit_destination_label(destinacion))}</A_DESTINACION_D>
<A_OBSERVACION>{_xml_value(credito.get('observacion'))}</A_OBSERVACION>
<A_PAGARE>{_xml_value(credito.get('pagare'))}</A_PAGARE>
<A_CODIGO_PROMOTOR>{_xml_value(credito.get('promotor'), '0')}</A_CODIGO_PROMOTOR>
<F_SOLICITUD>{_xml_value(credito.get('fechaSolicitud'))}</F_SOLICITUD>
<A_PERIODO_PAGO_CAPITAL>{_xml_value(credito.get('periodoPagoCapital'), '1')}</A_PERIODO_PAGO_CAPITAL>
<A_PERIODO_PAGO_INTERES>{_xml_value(credito.get('periodoPagoInteres'), '1')}</A_PERIODO_PAGO_INTERES>
<V_MONTOSOLICITADO>{_xml_value(credito.get('montoSolicitado'), '0')}</V_MONTOSOLICITADO>
<V_MONTO_MAX>{_xml_value(credito.get('montoMaximo'), '0')}</V_MONTO_MAX>
<V_MONTO_CREDITO>{_xml_value(credito.get('montoSolicitado'), '0')}</V_MONTO_CREDITO>
<V_MONTO_GIR>{_xml_value(credito.get('montoGiro'), '0')}</V_MONTO_GIR>
<V_NUMERO_CUOTAS>{_xml_value(credito.get('numeroCuotas'), '0')}</V_NUMERO_CUOTAS>
<V_TASA_NOMINAL>{_xml_value(credito.get('tasaNominal'), '0')}</V_TASA_NOMINAL>
<V_FACTOR_COMISION>{_xml_value(credito.get('factorComision'), '0')}</V_FACTOR_COMISION>
<A_VALOR_RECOGIDO>{_xml_value(credito.get('valorRecogido'), '0')}</A_VALOR_RECOGIDO>"""

    contactos_xml = _build_contactos_xml(data, cliente)
    socioeconomica_xml = _build_socioeconomica_xml(cliente)
    financiera_xml = _build_financiera_xml()
    laboral_xml = _build_laboral_xml(data)
    activos_xml = "<R_Activos></R_Activos>"
    pasivos_xml = "<R_Pasivos></R_Pasivos>"

    return ESTRUCTURA_CREDITO_WORKFLOW.format(
        DATOS=datos_xml,
        CONTACTOS=contactos_xml,
        SOCIOECONOMICA=socioeconomica_xml,
        FINANCIERA=financiera_xml,
        LABORAL=laboral_xml,
        ACTIVOS=activos_xml,
        PASIVOS=pasivos_xml,
    )


from incluirtec.services.vinculacion_defaults import affiliation_defaults


def _default_if_blank(value, default=""):
    if value is None:
        return default
    if isinstance(value, str) and value.strip() == "":
        return default
    return value


# incluirtec/services/xml_builder.py

def build_afiliacion_xml(data, defaults=None):
    """
    Construye el XML completo para el proceso de AFILIACIÓN
    Basado en la documentación oficial de LINIX
    """
    cliente = data.get('cliente', {})
    defaults = {**affiliation_defaults(), **(defaults or {})}
    fecha_aprobacion = _default_if_blank(
        data.get("fechaAprobacion"),
        _default_if_blank(data.get("fechaAfiliacion"), ""),
    )
    
    # ===== DATOS GENERALES (OBLIGATORIOS) =====
    # Estos son TODOS los campos obligatorios según la documentación
    datos_xml = f"""<A_CODIGO_PROMOTOR>{defaults["codigoPromotor"]}</A_CODIGO_PROMOTOR>
<A_REGIMEN_IVA>S</A_REGIMEN_IVA>
<F_APROBACION>{fecha_aprobacion}</F_APROBACION>
<A_TIPO_CON>D</A_TIPO_CON>
<A_AUTORETENEDOR>N</A_AUTORETENEDOR>
<A_REGIMEN_IMP_RENTA>T</A_REGIMEN_IMP_RENTA>
<O_RESAUT></O_RESAUT>
<A_FUNCIONALIDAD>6</A_FUNCIONALIDAD>
<A_EMAIL>{cliente.get('email', '')}</A_EMAIL>
<A_NUM_CELULAR>{cliente.get('numeroCelular', '')}</A_NUM_CELULAR>
<A_TELEFONO>{cliente.get('telefono', '1')}</A_TELEFONO>
<A_TIPO_DOC>{cliente.get('tipoDocumento', 'C')}</A_TIPO_DOC>
<A_CODIGO_CLIENTE>{cliente.get('identificacion')}</A_CODIGO_CLIENTE>
<A_PRIMER_APELLIDO>{cliente.get('primerApellido', '')}</A_PRIMER_APELLIDO>
<A_SEGUNDO_APELLIDO>{cliente.get('segundoApellido', '')}</A_SEGUNDO_APELLIDO>
<A_PRIMER_NOMBRE>{cliente.get('primerNombre', '')}</A_PRIMER_NOMBRE>
<A_SEGUNDO_NOMBRE>{cliente.get('segundoNombre', '')}</A_SEGUNDO_NOMBRE>
<F_EXPEDICION>{cliente.get('fechaExpedicion', '')}</F_EXPEDICION>
<A_CIUDAD_EXPEDA>{cliente.get('ciudadExpedicion', '')}</A_CIUDAD_EXPEDA>
<A_DEPART_EXPEDA>{cliente.get('departamentoExpedicion', '')}</A_DEPART_EXPEDA>
<A_PAIS_EXPEDA>{cliente.get('paisExpedicion', '169')}</A_PAIS_EXPEDA>
<F_NACIMIENTO>{cliente.get('fechaNacimiento', '')}</F_NACIMIENTO>
<A_ESTADO_CIVIL>{cliente.get('estadoCivil', 'S')}</A_ESTADO_CIVIL>
<A_IDENTIFICACION>{cliente.get('identificacion')}</A_IDENTIFICACION>
<A_GENERO>{cliente.get('genero', 'M')}</A_GENERO>
<A_NATURALEZA>N</A_NATURALEZA>
<A_FACTOR_RH>{cliente.get('factorRH', '')}</A_FACTOR_RH>
<A_ACTIVO>Y</A_ACTIVO>
<A_NOMBRE>{cliente.get('primerApellido', '')} {cliente.get('segundoApellido', '')} {cliente.get('primerNombre', '')} {cliente.get('segundoNombre', '')}</A_NOMBRE>
<A_ESTRATO>{cliente.get('estrato', '3')}</A_ESTRATO>
<A_NOMINA>{defaults["nomina"]}</A_NOMINA>
<A_SUCURSAL>{data.get('sucursal', '102')}</A_SUCURSAL>
<A_ASOCON>{defaults["asocon"]}</A_ASOCON>
<A_CENCOS>{defaults["cencos"]}</A_CENCOS>
<A_SECCION>{defaults["seccion"]}</A_SECCION>
<A_UBICACION_UNO>{defaults["ubicacionUno"]}</A_UBICACION_UNO>
<A_UBICACION_DOS>{defaults["ubicacionDos"]}</A_UBICACION_DOS>
<A_BANCO>{cliente.get('codigoBanco', '')}</A_BANCO>
<A_TIPO_CUENTA>{cliente.get('tipoCuenta', 'A')}</A_TIPO_CUENTA>
<A_NUM_CUENTA>{cliente.get('numeroCuenta', '')}</A_NUM_CUENTA>
<A_PAIS_IDENTIFICACION>169</A_PAIS_IDENTIFICACION>
<A_DEPARTAMENTO_IDENTI>{cliente.get('departamentoExpedicion', '')}</A_DEPARTAMENTO_IDENTI>
<A_CIUDAD_IDENTIFICACION>{cliente.get('ciudadExpedicion', '')}</A_CIUDAD_IDENTIFICACION>
<A_ESTADO_CLIENTE>A</A_ESTADO_CLIENTE>
<A_CLIENTE_ASOCIADO>A</A_CLIENTE_ASOCIADO>
<A_TIPASO>1</A_TIPASO>
<F_PRIMERA_AFILIA>{data.get('fechaAfiliacion', '')}</F_PRIMERA_AFILIA>
<F_ULTIMA_ACTUALIZACION>{data.get('fechaAfiliacion', '')}</F_ULTIMA_ACTUALIZACION>
<F_ULTIMA_AFILIA>{data.get('fechaAfiliacion', '')}</F_ULTIMA_AFILIA>
<F_ANTIGUEDAD>{data.get('fechaAfiliacion', '')}</F_ANTIGUEDAD>
<V_CALIFI_ASOCIA></V_CALIFI_ASOCIA>
<A_DESCUENTO_NOMINA></A_DESCUENTO_NOMINA>
<A_CODIGO_LISTA></A_CODIGO_LISTA>
<A_UNIDAD_EJECUCION>{defaults["unidadEjecucion"]}</A_UNIDAD_EJECUCION>
<A_TIPO_VIVIENDA>{cliente.get('tipoVivienda', 'P')}</A_TIPO_VIVIENDA>
<A_ACTIVIDAD_ECONOMICA>{cliente.get('actividadEconomica', 'EM')}</A_ACTIVIDAD_ECONOMICA>
<A_EMPRESA>0</A_EMPRESA>
<V_CODIGO_ASOCIADO>{cliente.get('identificacion')}</V_CODIGO_ASOCIADO>
<F_INGRESO>{cliente.get('fechaIngreso', '')}</F_INGRESO>
<A_TIPO_CONTRATO>{cliente.get('tipoContrato', 'TI')}</A_TIPO_CONTRATO>
<F_VENCIMIENTO>{cliente.get('fechaVencimientoContrato', '')}</F_VENCIMIENTO>
<A_CARGO>{defaults["cargo"]}</A_CARGO>
<A_CODIGO_CARGO>{defaults["codigoCargo"]}</A_CODIGO_CARGO>
<A_DEPENDENCIA>{defaults["dependencia"]}</A_DEPENDENCIA>
<A_JORNADA_LABORAL>{cliente.get('jornadaLaboral', '1')}</A_JORNADA_LABORAL>
<A_SALARIO_INTEGRAL>{cliente.get('salarioIntegral', 'N')}</A_SALARIO_INTEGRAL>
<A_AUTORIZA_INFCENTRAL>{cliente.get('autorizaCentrales', 'Y')}</A_AUTORIZA_INFCENTRAL>
<A_AUTORIZA_NOTIFICA>{cliente.get('autorizaNotificacion', 'Y')}</A_AUTORIZA_NOTIFICA>
<V_SUELDO_ASOC>{cliente.get('salario', '0')}</V_SUELDO_ASOC>
<A_CODIGO_CONCEPTO>{defaults["codigoConcepto"]}</A_CODIGO_CONCEPTO>"""

    # ===== R_ESTATUTARIAS (Multiregistro OBLIGATORIO) =====
    estatutarias_xml = """<R_ESTATUTARIAS>
<R_REGISTROESTATUTARIAS V_VALOR_FACTOR="50000" V_PORCENTAJE_APORTE=""></R_REGISTROESTATUTARIAS>
</R_ESTATUTARIAS>"""

    # ===== R_CAPACIDAD (Multiregistro) =====
    capacidad_xml = f"""<R_CAPACIDAD>
<R_REGISTROCAPACIDAD A_CODIGO_CONCEPTO="{defaults['codigoConcepto']}" A_CODIGO_CONCEPTO_D="Sueldo básico (SB)" V_VALOR_CONCEPTO="{cliente.get('salario', '0')}"></R_REGISTROCAPACIDAD>
</R_CAPACIDAD>"""

    # ===== R_CONTACTO (Multiregistro OBLIGATORIO) =====
    contactos_xml = "<R_Contacto>\n"
    contactos = data.get("contactos", [])
    
    if not contactos:
        # Contacto por defecto si no se envía ninguno
        contactos_xml += f"""<R_RegistroContacto A_TIPO_DIRECCION="C" A_DIRECCION_CONTACTO="CALLE 1 # 1-1" A_TELEFONO_CONTACTO="{cliente.get('telefono', '1234567')}" A_MOVIL_CONTACTO="{cliente.get('numeroCelular', '3001234567')}" A_EMAIL_CONTACTO="{cliente.get('email', 'default@mail.com')}" A_CIUDAD_DIR="11001" A_EXTENSION_TEL="" A_INDICATIVO_TEL="057" A_BARRIO="CENTRO" A_DEFINIR_DIR_CORRESPONDENCIA="Y"></R_RegistroContacto>\n"""
    else:
        for c in contactos:
            contactos_xml += f"""<R_RegistroContacto A_TIPO_DIRECCION="{c.get('tipoDireccion', 'C')}" A_DIRECCION_CONTACTO="{c.get('direccion', 'CALLE 1')}" A_TELEFONO_CONTACTO="{c.get('telefono', '1234567')}" A_MOVIL_CONTACTO="{c.get('movil', '3001234567')}" A_EMAIL_CONTACTO="{c.get('email', 'default@mail.com')}" A_CIUDAD_DIR="{c.get('ciudad', '11001')}" A_EXTENSION_TEL="{c.get('extension', '')}" A_INDICATIVO_TEL="{c.get('indicativo', '057')}" A_BARRIO="{c.get('barrio', 'CENTRO')}" A_DEFINIR_DIR_CORRESPONDENCIA="{c.get('direccionCorrespondencia', 'Y')}"></R_RegistroContacto>\n"""
    
    contactos_xml += "</R_Contacto>"

    # ===== R_PERSONAL (Multiregistro) =====
    personal_xml = f"""<R_Personal>
<R_RegistroPersonal F_NACIMIENTO="{cliente.get('fechaNacimiento', '')}" A_CIUDAD_NACIMIENTO="{cliente.get('ciudadNacimiento', '')}" A_GENERO="{cliente.get('genero', 'M')}" A_ESTADO_CIVIL="{cliente.get('estadoCivil', 'S')}" A_CIUDAD_IDENTIFICACION="{cliente.get('ciudadExpedicion', '')}" F_FECHA_IDENTIFICACION="{cliente.get('fechaExpedicion', '')}" A_FACTOR_RH="{cliente.get('factorRH', '')}" A_RAZON_SOCIAL="" A_SIGLA="" A_TIPOEMP="" A_TIPO_IDENTIFICACION="" A_NUM_REP_LEGAL="" A_REP_LEGAL="" A_CAMARA_COMERCIO="" A_ESCRITURA_DE="" F_ESCRITURA="" V_NOTARIA="" V_CAPITAL_PAGADO="" A_CODIGO_CLIENTE="" A_AUTORIZA_NOTIFICACION="{cliente.get('autorizaNotificacion', 'Y')}" A_AUTORIZA_CENTRALRIESGO="{cliente.get('autorizaCentrales', 'Y')}"></R_RegistroPersonal>
</R_Personal>"""

    # ===== R_TRANSFERENCIA (Multiregistro) =====
    transferencia_xml = f"""<R_Transferencia>
<R_RegistroTransferencia A_BANCO="{cliente.get('codigoBanco', '')}" A_NUM_CUENTA="{cliente.get('numeroCuenta', '')}" A_TIPCNTA="{cliente.get('tipoCuenta', 'A')}" A_DESCUENTO_NOMINA="N"></R_RegistroTransferencia>
</R_Transferencia>"""

    # ===== R_SOCIOECONOMICA (Multiregistro OBLIGATORIO) =====
    socioeconomica_xml = f"""<R_Socioeconomica>
<R_RegistroSocioeconomica A_NIVEL_ESTUDIO="{cliente.get('nivelEstudio', 'S')}" A_PROFESION="" A_TIPO_VIVIENDA="{cliente.get('tipoVivienda', 'P')}" A_ESTRATO="{cliente.get('estrato', '3')}" A_MUJER_CABEZA="{cliente.get('mujerCabeza', 'N')}" A_ACTIVIDAD_ECONOMICA="{cliente.get('actividadEconomica', 'EM')}" A_RESPONSABLE_HOGAR="{cliente.get('responsableHogar', 'N')}" A_OCUPACION="{cliente.get('ocupacion', '1')}" A_POBVULNERABLE="{cliente.get('poblacionVulnerable', 'N')}" A_NUM_PERCARGO="{cliente.get('personasCargo', '0')}" A_TIPOEMP="" A_PUBEXP="{cliente.get('publicamenteExpuesto', 'N')}" O_PROFES="" A_ACTIVIDAD_CIIU="{cliente.get('actividadCIIU', '0122')}" A_ACTIVIDAD_CIIU_SECU="{cliente.get('actividadCIIUSecundaria', '0121')}" A_CUAL="" A_NOMBRE_VIVIENDA="" A_PARENTESCO="" A_TELEFONO="" A_BANCO="" A_CODIGO_BANCO="" F_VENCIMIENTO_HIPOTECA=""></R_RegistroSocioeconomica>
</R_Socioeconomica>"""

    # ===== R_FINANCIERA (Multiregistro OBLIGATORIO) =====
    financiera_xml = f"""<R_Financiera>
<R_RegistroFinanciera A_OPERACIONES_MONEXT="{cliente.get('operacionesMonedaExtranjera', 'N')}" A_DESCRIPCION_OPEEXT="" A_CUENTA_EXTERIOR="N" A_PAIS_EXTRANJERA="" A_BANCO_EXTRANJERA="" A_MONEDA="" A_NUMERO_CNTAEXT="" A_DECLARA_RENTA="{cliente.get('declaraRenta', 'N')}" A_ADMINTRA_RECURSOS="{cliente.get('administraRecursos', 'N')}" A_CARGO_RECPU="" A_ENTIDAD_RECPU="" I_VINCULADO_RECPU="" F_DESVINCULA_RECPU="" F_VINCULACION_RECPU="" A_OTRING=""></R_RegistroFinanciera>
</R_Financiera>"""

    # ===== R_LABORAL (Multiregistro) =====
    laboral = data.get('laboral', {})
    laboral_xml = f"""<R_Laboral>
<R_RegistroLaboral V_CODIGOEMPLEADO_EMPRESA="{laboral.get('codigoEmpleado', '')}" A_EMPRESA="{laboral.get('nombreEmpresa', '')}" A_TIPO_CONTRATO="{laboral.get('tipoContrato', 'TI')}" A_TIPO_CONTRATO_D="Término Indefinido" F_INGRESO="{laboral.get('fechaIngreso', '')}" F_VENCIMIENTO="{laboral.get('fechaVencimiento', '')}" A_SALARIO_INTEGRAL="{laboral.get('salarioIntegral', 'N')}" A_CARGO="{defaults['cargo']}" A_DEPENDENCIA="{defaults['dependencia']}" A_DEPENDENCIA_D="No aplica" A_JORNADA_LABORAL="{laboral.get('jornadaLaboral', '1')}" A_JORNADA_LABORAL_D="Tiempo total" A_MEDIO_PAGO="{defaults['medioPago']}" A_MEDIO_PAGO_D="VENTANILLA" A_CODIGO_CIUDAD_EMPRESA="{laboral.get('ciudadEmpresa', '')}" A_CODIGO_DEPART_EMPRESA="{laboral.get('departamentoEmpresa', '')}" A_CODIGO_PAIS_EMPRESA="{laboral.get('paisEmpresa', '169')}" A_CIUDAD_DEPARTAMENTO="" A_TELEFONO_EMPRESA="{laboral.get('telefonoEmpresa', '')}" A_DIRECCION_EMPRESA="{laboral.get('direccionEmpresa', '')}" A_TELEFONO_FAX_EMPRESA="{laboral.get('faxEmpresa', '')}" A_FORMALIDAD_NEGOCIO="{laboral.get('formalidadNegocio', 'NA')}"></R_RegistroLaboral>
</R_Laboral>"""

    # ===== R_ACTIVOS (Multiregistro - puede estar vacío) =====
    activos_xml = "<R_Activos>"
    activos = data.get("activos", [])
    for idx, activo in enumerate(activos):
        activos_xml += f"""<R_RegistroActivos V_CODIGO_ACTIVO="-1_{idx}" A_TIPO_ACTIVO="{activo.get('tipo', 'OT')}" A_TIPO_ACTIVO_D="{activo.get('tipoDescripcion', 'Otros')}" A_DESCRIPCION_ACTIVO="{activo.get('descripcion', '')}" V_VALOR_COMERCIAL="{activo.get('valorComercial', '0')}" V_SALDO_PIGNORACION="{activo.get('saldoPignoracion', '0')}" A_ENTIDAD_PIGNORACION="{activo.get('entidadPignoracion', '')}" V_CUOTA_PIGNORACION="{activo.get('cuotaPignoracion', '0')}" A_OBSERVACION_ACTIVO="{activo.get('observacion', '')}" A_ACCION="I"></R_RegistroActivos>"""
    activos_xml += "</R_Activos>"

    # ===== R_PASIVOS (Multiregistro - puede estar vacío) =====
    pasivos_xml = "<R_Pasivos>"
    pasivos = data.get("pasivos", [])
    for idx, pasivo in enumerate(pasivos):
        pasivos_xml += f"""<R_RegistroPasivos V_CODIGO_PASIVO="-1_{idx}" A_TIPO_PASIVO="{pasivo.get('tipo', 'CC')}" A_TIPO_PASIVO_D="{pasivo.get('tipoDescripcion', 'Credito Consumo')}" V_SALDO_PASIVO="{pasivo.get('saldo', '0')}" A_ENTIDAD_PASIVO="{pasivo.get('entidad', '')}" V_CUOTA_PASIVO="{pasivo.get('cuota', '0')}" F_FIN_PASIVO="{pasivo.get('fechaFin', '')}" A_OBSERVACION_PASIVO="{pasivo.get('observacion', '')}" A_ACCION="I"></R_RegistroPasivos>"""
    pasivos_xml += "</R_Pasivos>"

    # ===== CONSTRUIR XML FINAL COMPLETO =====
    xml_completo = f"""<Entrada>
<Estructura>
<General>A_CODIGO_PROMOTOR,A_REGIMEN_IVA,F_APROBACION,A_TIPO_CON,A_AUTORETENEDOR,A_REGIMEN_IMP_RENTA,O_RESAUT,A_FUNCIONALIDAD,A_EMAIL,A_NUM_CELULAR,A_TELEFONO,A_TIPO_DOC,A_CODIGO_CLIENTE,A_PRIMER_APELLIDO,A_SEGUNDO_APELLIDO,A_PRIMER_NOMBRE,A_SEGUNDO_NOMBRE,F_EXPEDICION,A_CIUDAD_EXPEDA,A_DEPART_EXPEDA,A_PAIS_EXPEDA,F_NACIMIENTO,A_ESTADO_CIVIL,A_IDENTIFICACION,A_GENERO,A_NATURALEZA,A_FACTOR_RH,A_ACTIVO,A_NOMBRE,A_ESTRATO,A_NOMINA,A_SUCURSAL,A_ASOCON,A_CENCOS,A_SECCION,A_UBICACION_UNO,A_UBICACION_DOS,A_BANCO,A_TIPO_CUENTA,A_NUM_CUENTA,A_PAIS_IDENTIFICACION,A_DEPARTAMENTO_IDENTI,A_CIUDAD_IDENTIFICACION,A_ESTADO_CLIENTE,A_CLIENTE_ASOCIADO,A_TIPASO,F_PRIMERA_AFILIA,F_ULTIMA_ACTUALIZACION,F_ULTIMA_AFILIA,F_ANTIGUEDAD,V_CALIFI_ASOCIA,A_DESCUENTO_NOMINA,A_CODIGO_LISTA,A_UNIDAD_EJECUCION,A_TIPO_VIVIENDA,A_ACTIVIDAD_ECONOMICA,A_EMPRESA,V_CODIGO_ASOCIADO,F_INGRESO,A_TIPO_CONTRATO,F_VENCIMIENTO,A_CARGO,A_CODIGO_CARGO,A_DEPENDENCIA,A_JORNADA_LABORAL,A_SALARIO_INTEGRAL,A_AUTORIZA_INFCENTRAL,A_AUTORIZA_NOTIFICA,V_SUELDO_ASOC,A_CODIGO_CONCEPTO,R_ESTATUTARIAS[],R_CAPACIDAD[],R_Contacto[],R_Personal[],R_Transferencia[],R_Socioeconomica[],R_Financiera[],R_Laboral[],R_Activos[],R_Pasivos[]</General>
<Individual>
<EstructuraR_ESTATUTARIAS>R_REGISTROESTATUTARIAS[]</EstructuraR_ESTATUTARIAS>
<EstructuraR_REGISTROESTATUTARIAS>V_VALOR_FACTOR,V_PORCENTAJE_APORTE</EstructuraR_REGISTROESTATUTARIAS>
<EstructuraR_CAPACIDAD>R_REGISTROCAPACIDAD[]</EstructuraR_CAPACIDAD>
<EstructuraR_REGISTROCAPACIDAD>A_CODIGO_CONCEPTO,A_CODIGO_CONCEPTO_D,V_VALOR_CONCEPTO</EstructuraR_REGISTROCAPACIDAD>
<EstructuraR_Socioeconomica>R_RegistroSocioeconomica[]</EstructuraR_Socioeconomica>
<EstructuraR_RegistroSocioeconomica>A_NIVEL_ESTUDIO,A_PROFESION,A_TIPO_VIVIENDA,A_ESTRATO,A_MUJER_CABEZA,A_ACTIVIDAD_ECONOMICA,A_RESPONSABLE_HOGAR,A_OCUPACION,A_POBVULNERABLE,A_NUM_PERCARGO,A_TIPOEMP,A_PUBEXP,O_PROFES,A_ACTIVIDAD_CIIU,A_ACTIVIDAD_CIIU_SECU,A_CUAL,A_NOMBRE_VIVIENDA,A_PARENTESCO,A_TELEFONO,A_BANCO,A_CODIGO_BANCO,F_VENCIMIENTO_HIPOTECA</EstructuraR_RegistroSocioeconomica>
<EstructuraR_Personal>R_RegistroPersonal[]</EstructuraR_Personal>
<EstructuraR_RegistroPersonal>F_NACIMIENTO,A_CIUDAD_NACIMIENTO,A_GENERO,A_ESTADO_CIVIL,A_CIUDAD_IDENTIFICACION,F_FECHA_IDENTIFICACION,A_AUTORIZA_NOTIFICACION,A_AUTORIZA_CENTRALRIESGO,A_RAZON_SOCIAL,A_SIGLA,A_TIPOEMP,A_TIPO_IDENTIFICACION,A_NUM_REP_LEGAL,A_REP_LEGAL,A_CAMARA_COMERCIO,A_ESCRITURA_DE,F_ESCRITURA,V_NOTARIA,V_CAPITAL_PAGADO,A_CODIGO_CLIENTE,A_FACTOR_RH</EstructuraR_RegistroPersonal>
<EstructuraR_Transferencia>R_RegistroTransferencia[]</EstructuraR_Transferencia>
<EstructuraR_RegistroTransferencia>A_BANCO,A_NUM_CUENTA,A_TIPCNTA,A_DESCUENTO_NOMINA</EstructuraR_RegistroTransferencia>
<EstructuraR_Contacto>R_RegistroContacto[]</EstructuraR_Contacto>
<EstructuraR_RegistroContacto>A_TIPO_DIRECCION,A_DIRECCION_CONTACTO,A_TELEFONO_CONTACTO,A_MOVIL_CONTACTO,A_EMAIL_CONTACTO,A_CIUDAD_DIR,A_EXTENSION_TEL,A_INDICATIVO_TEL,A_BARRIO,A_DEFINIR_DIR_CORRESPONDENCIA</EstructuraR_RegistroContacto>
<EstructuraR_Financiera>R_RegistroFinanciera[]</EstructuraR_Financiera>
<EstructuraR_RegistroFinanciera>A_OPERACIONES_MONEXT,A_CUENTA_EXTERIOR,A_DESCRIPCION_OPEEXT,A_PAIS_EXTRANJERA,A_BANCO_EXTRANJERA,A_MONEDA,A_NUMERO_CNTAEXT,A_DECLARA_RENTA,A_ADMINTRA_RECURSOS,A_CARGO_RECPU,A_ENTIDAD_RECPU,I_VINCULADO_RECPU,F_DESVINCULA_RECPU,F_VINCULACION_RECPU,A_OTRING</EstructuraR_RegistroFinanciera>
<EstructuraR_Laboral>R_RegistroLaboral[]</EstructuraR_Laboral>
<EstructuraR_RegistroLaboral>V_CODIGOEMPLEADO_EMPRESA,A_EMPRESA,A_TIPO_CONTRATO,A_TIPO_CONTRATO_D,F_INGRESO,F_VENCIMIENTO,A_SALARIO_INTEGRAL,A_CARGO,A_DEPENDENCIA,A_DEPENDENCIA_D,A_JORNADA_LABORAL,A_JORNADA_LABORAL_D,A_MEDIO_PAGO,A_MEDIO_PAGO_D,A_CODIGO_CIUDAD_EMPRESA,A_CODIGO_DEPART_EMPRESA,A_CODIGO_PAIS_EMPRESA,A_CIUDAD_DEPARTAMENTO,A_TELEFONO_EMPRESA,A_DIRECCION_EMPRESA,A_TELEFONO_FAX_EMPRESA,A_FORMALIDAD_NEGOCIO</EstructuraR_RegistroLaboral>
<EstructuraR_Activos>R_RegistroActivos[]</EstructuraR_Activos>
<EstructuraR_RegistroActivos>V_CODIGO_ACTIVO,A_TIPO_ACTIVO,A_TIPO_ACTIVO_D,A_DESCRIPCION_ACTIVO,V_VALOR_COMERCIAL,V_SALDO_PIGNORACION,A_ENTIDAD_PIGNORACION,V_CUOTA_PIGNORACION,A_OBSERVACION_ACTIVO,A_ACCION</EstructuraR_RegistroActivos>
<EstructuraR_Pasivos>R_RegistroPasivos[]</EstructuraR_Pasivos>
<EstructuraR_RegistroPasivos>V_CODIGO_PASIVO,A_TIPO_PASIVO,A_TIPO_PASIVO_D,V_SALDO_PASIVO,A_ENTIDAD_PASIVO,V_CUOTA_PASIVO,F_FIN_PASIVO,A_OBSERVACION_PASIVO,A_ACCION</EstructuraR_RegistroPasivos>
</Individual>
</Estructura>
<Datos>
{datos_xml}
{estatutarias_xml}
{capacidad_xml}
{contactos_xml}
{personal_xml}
{transferencia_xml}
{socioeconomica_xml}
{financiera_xml}
{laboral_xml}
{activos_xml}
{pasivos_xml}
</Datos>
</Entrada>"""

    return xml_completo

