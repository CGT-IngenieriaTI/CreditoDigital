"""Exact, case-bound CD16 variant B. Preparation never opens Oracle or grants credit."""

from copy import deepcopy
from hashlib import sha256
from importlib.util import find_spec
from uuid import UUID

from .cd16_xml import prepare_sc_input_variants, verify_sc_input_variant
from .contracts import CREDIHOY_ADMIN_VERSION, canonical, normalize_request, normalize_technical_request
from .credihoy import build_radication_xml
from .references import normalize_references, reference_catalog


LEGACY_VERSION = "credihoy-ensayo-productivo-b-v1"
VERSION = "credihoy-preparacion-tecnica-b-v2"
PILOT_REQUEST = "a402121e-81d0-4759-8c4c-485f0787d45c"
PILOT_INTENT = "0082f640-23fe-4ebd-b94a-99805dd3de60"
PROOF_FIELDS = ("consentimiento", "referencias", "condicionesComerciales", "simulacion", "elegibilidadAdministrativa")


def digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def builder_version():
    names = ("digital_operations/credihoy.py", "digital_operations/cd16_xml.py",
             "digital_operations/references.py", "digital_operations/production_preparation.py",
             "digital_operations/production_preflight.py", "digital_operations/credihoy_oracle.py",
             "digital_operations/credihoy_repository.py",
             "digital_operations/oracle_readonly.py",
             "digital_operations/contracts.py", "services/xml_builder.py", "services/xml_templates.py",
             "services/credit_line_config.py", "services/credit_validators.py", "services/microcredito_plan.py",
             "services/vinculacion_defaults.py")
    versions = {}
    for name in names:
        spec = find_spec("incluirtec." + name.removesuffix(".py").replace("/", "."))
        versions[name] = sha256(spec.loader.get_data(spec.origin)).hexdigest()
    return versions


def _prepare_v1(payload, references, *, evidence, authorization):
    """Historical sealed records only; not the default for any new preparation."""
    normalized = normalize_request(payload, allow_without_promoter_offline=True)
    if (normalized["versionContrato"] != CREDIHOY_ADMIN_VERSION
            or normalized["solicitudId"] == PILOT_REQUEST or normalized["intencionId"] == PILOT_INTENT):
        raise ValueError("ENSAYO_REQUIERE_EXPEDIENTE_E_INTENCION_DISTINTOS")
    if not isinstance(evidence, dict) or set(evidence) != set(PROOF_FIELDS):
        raise ValueError("EVIDENCIA_DEL_EXPEDIENTE_INCOMPLETA")
    for proof in evidence.values():
        if (not isinstance(proof, dict) or set(proof) != {"referencia", "sha256"}
                or not isinstance(proof["referencia"], str) or not proof["referencia"].strip()
                or not isinstance(proof["sha256"], str) or len(proof["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in proof["sha256"])):
            raise ValueError("EVIDENCIA_DEL_EXPEDIENTE_INVALIDA")
    if (not isinstance(authorization, dict)
            or set(authorization) != {"solicitudId", "intencionId", "responsable", "referencia", "antecedente"}
            or authorization["solicitudId"] != normalized["solicitudId"]
            or authorization["intencionId"] != normalized["intencionId"]
            or authorization["referencia"] != normalized["autorizacionAdministrativa"]["referencia"]
            or any(not isinstance(authorization[key], str) or not authorization[key].strip()
                   for key in ("responsable", "referencia", "antecedente"))):
        raise ValueError("AUTORIZACION_ESPECIFICA_NO_CORRESPONDE")
    records = normalize_references(references)
    if evidence["referencias"]["sha256"] != digest(canonical(records)):
        raise ValueError("REFERENCIAS_NO_CORRESPONDEN_A_EVIDENCIA")
    original = build_radication_xml(normalized, allow_without_promoter_offline=True,
                                    references_for_preparation=records)
    variants = prepare_sc_input_variants(original)
    return {"versionContrato": LEGACY_VERSION, "payload": normalized, "referencias": records,
            "xmlOriginal": original, "xml": variants.candidate, "xmlSha256": digest(variants.candidate),
            "delta": variants.report(), "builderVersion": builder_version(),
            "catalogoSha256": reference_catalog()[1], "evidencias": deepcopy(evidence),
            "autorizacion": deepcopy(authorization), "variante": "B", "habilitadoDecisionProductiva": False}


def prepare(payload, references, *, evidence, authorization=None):
    """Prepare structural operands, never administrative grants or Oracle execution."""
    normalized = normalize_technical_request(payload)
    if normalized["solicitudId"] == PILOT_REQUEST or normalized["intencionId"] == PILOT_INTENT:
        raise ValueError("ENSAYO_REQUIERE_EXPEDIENTE_E_INTENCION_DISTINTOS")
    consent = evidence.get("consentimiento") if isinstance(evidence, dict) else None
    if (not isinstance(consent, dict) or set(consent) != {"referencia", "sha256"}
            or not isinstance(consent["referencia"], str) or not consent["referencia"].strip()
            or not isinstance(consent["sha256"], str) or len(consent["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in consent["sha256"])):
        raise ValueError("SOPORTE_CONSENTIMIENTO_REQUERIDO")
    records = normalize_references(references)
    original = build_radication_xml(normalized, allow_without_promoter_offline=True,
                                    references_for_preparation=records)
    variants = prepare_sc_input_variants(original)
    return {"versionContrato": VERSION, "payload": normalized, "referencias": records,
            "xmlOriginal": original, "xml": variants.candidate, "xmlSha256": digest(variants.candidate),
            "delta": variants.report(), "builderVersion": builder_version(),
            "catalogoSha256": reference_catalog()[1], "referenciasSha256": digest(canonical(records)),
            "antecedentes": {"evidencias": deepcopy(evidence), "autorizacion": deepcopy(authorization)},
            "variante": "B", "habilitadoDecisionProductiva": False,
            "alcance": "PREPARACION_TECNICA_LOCAL_SIN_TRANSPORTE",
            "consentimientosLegalesEvaluados": False,
            "contencionEfectosInternos": "NO_ACREDITADA"}


def verify(prepared):
    if not isinstance(prepared, dict) or prepared.get("versionContrato") not in (VERSION, LEGACY_VERSION):
        raise ValueError("PREPARACION_B_REQUERIDA")
    if prepared["versionContrato"] == LEGACY_VERSION:
        rebuilt = _prepare_v1(prepared["payload"], prepared["referencias"], evidence=prepared["evidencias"],
                              authorization=prepared["autorizacion"])
    else:
        rebuilt = prepare(prepared["payload"], prepared["referencias"],
                          evidence=prepared["antecedentes"]["evidencias"],
                          authorization=prepared["antecedentes"]["autorizacion"])
    if canonical(rebuilt) != canonical(prepared):
        raise ValueError("XML_CONDICIONES_O_VERSION_CAMBIARON")
    verify_sc_input_variant(prepared["xmlOriginal"], prepared["xml"])
    return prepared["xml"]


def selected_request(value):
    result = str(UUID(str(value)))
    if result == PILOT_REQUEST:
        raise ValueError("PILOTO_PROTEGIDO_SOLO_CONCILIACION")
    return result


def prepare_personal(source,claim):
    """Feed the existing builder with authenticated accepted terms, never defaults."""
    import re
    from decimal import Decimal
    required={'payload','referencias','evidencias','perfilCore','preflight','simulacionAceptada'}
    if type(source) is not dict or set(source)!=required:
        raise ValueError('CD6_ENTRADA_PERSONAL_INCOMPLETA')
    payload=normalize_technical_request(source['payload']);proof=source['simulacionAceptada']
    try:
        if (type(proof) is not dict or payload['solicitudId']!=claim['solicitudId']
                or payload['intencionId']!=claim['intencionId']
                or proof['solicitudId']!=payload['solicitudId']
                or proof['titularReferencia']!=sha256(payload['titular']['numeroDocumento'].encode()).hexdigest()
                or proof['titularReferencia']!=claim['titularSha256']
                or proof['autorizaStart'] is not False
                or type(proof['aceptacionAuditoriaId']) is not int or proof['aceptacionAuditoriaId']<=0
                or any(str(UUID(proof[k]))!=proof[k] for k in ('revisionReferencia','decisionReferencia'))
                or any(not re.fullmatch('[a-f0-9]{64}',proof[k]) for k in
                    ('decisionSha256','versionCondiciones','simulacionSha256','aceptacionSha256'))
                or Decimal(proof['cuota'])!=Decimal(payload['credito']['cuotaContractual'])
                or Decimal(proof['monto'])!=Decimal(payload['credito']['montoSolicitado'])
                or Decimal(proof['tasaNominalNamv'])!=Decimal(payload['credito']['tasaNominalAnual'])
                or proof['plazoMeses']!=payload['credito']['plazoMeses']
                or proof['periodicidad']!=payload['credito']['periodicidad']
                or (payload['producto']['codigo'],payload['producto']['modalidad'],payload['producto']['destino'])!=('390','111','CFA')):
            raise ValueError()
        profile=source['perfilCore']
        if (type(profile) is not dict or set(profile)!={'sucursal','nomina','tipoNomina'}
                or any(type(v) is not str or not v for v in profile.values())
                or profile['sucursal']!=payload['contextoLinix']['sucursal']):raise ValueError()
    except (KeyError,ValueError,TypeError,AttributeError,ArithmeticError):
        raise ValueError('CD6_CONDICIONES_NO_CORRESPONDEN_A_ACEPTACION_PERSONAL') from None
    if 'entrada113' not in payload['contextoLinix']:
        raise ValueError('CD6_SEMANTICA_ENTRADA_113_REQUERIDA')
    from .production_preflight import require_configuration
    require_configuration(source['preflight'])
    prepared=prepare(payload,source['referencias'],evidence=source['evidencias'])
    verify(prepared)
    return {'preparacion':prepared,'perfilCore':deepcopy(profile),'preflight':deepcopy(source['preflight']),
        'canal':'CONFIRMACION_PERSONAL_CD6','aceptacionSimulacion':deepcopy(proof)}


def resolve_personal_source(source,claim,port,policy):
    """Resolve only missing native entry operands through the existing CGTE reader."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from .credihoy_repository import IDENTITY_BY_DOCUMENT_SQL,PAYROLL_BY_HOLDER_SQL,PERSONAL_IDENTITY_NAME_SQL
    if type(source) is not dict:raise ValueError('CD6_ENTRADA_PERSONAL_INCOMPLETA')
    result=deepcopy(source)
    try:
        payload=result['payload'];context=payload['contextoLinix'];params=context['parametros']
        proof=result['simulacionAceptada'];document=payload['titular']['numeroDocumento']
        if (payload['solicitudId']!=claim['solicitudId'] or proof['solicitudId']!=claim['solicitudId']
                or sha256(document.encode()).hexdigest()!=claim['titularSha256']
                or proof['titularReferencia']!=claim['titularSha256']):raise ValueError()
    except (KeyError,ValueError,TypeError,AttributeError):raise ValueError('CD6_TITULAR_DE_ENTRADA_NO_ACREDITADO') from None
    if result.get('preflight') is not None and result.get('perfilCore') is not None and context.get('kIdTerc'):
        return result
    chosen=policy.get('parametrosProducto')
    if type(chosen) is not dict or not policy.get('referenciaParametros'):
        raise ValueError('CD6_PERFIL_PARAMETROS_PRODUCTO_PENDIENTE')
    for name in ('amortizacion','formaAplicarTasa','seguroVida','modalidadCapital',
            'porcentajeCapitalizacion','valorTotalCapitalizacion','periodoPagoCapital','periodoPagoInteres'):
        selected=chosen.get(name)
        if type(selected) is not str or not selected:raise ValueError('CD6_PARAMETRO_PRODUCTO_PENDIENTE_'+name.upper())
        if params.get(name) is not None and params[name]!=selected:
            raise ValueError('CD6_PARAMETRO_PRODUCTO_NO_CONCORDANTE_'+name.upper())
        params[name]=selected
    if params.get('actividadEconomica') is None:
        if proof['clasificacionRegistrada']=='REQUIERE_CONSEJO_ADMINISTRACION':
            params['actividadEconomica']=policy.get('actividadColaborador')
        if not params.get('actividadEconomica'):raise ValueError('CD6_ACTIVIDAD_ECONOMICA_PENDIENTE')
    if not context.get('fechaCuotaOrdinaria'):raise ValueError('CD6_PRIMERA_FECHA_PROPUESTA_REQUERIDA')
    context['fechaSolicitud']=datetime.now(ZoneInfo('America/Bogota')).strftime('%m/%d/%Y')
    connection=port.open_connection();cursor=None
    try:
        cursor=connection.cursor()
        cursor.execute(IDENTITY_BY_DOCUMENT_SQL,{'documento':document});identity=cursor.fetchmany(2)
        if len(identity)!=1 or identity[0][1]!='Y':raise ValueError('CD6_IDENTIDAD_INSTITUCIONAL_NO_UNICA')
        third=str(identity[0][0])
        if context.get('kIdTerc') not in (None,third):raise ValueError('CD6_TERCERO_INSTITUCIONAL_NO_CONCORDANTE')
        context['kIdTerc']=third
        cursor.execute(PAYROLL_BY_HOLDER_SQL,{'tercero':int(third)});rows=cursor.fetchmany(2)
        if len(rows)!=1 or rows[0][3]!='Y':raise ValueError('CD6_AGENCIA_NOMINA_NO_ACREDITADA')
        branch,payroll,kind=(str(v) for v in rows[0][:3])
        if context.get('sucursal') not in (None,branch) or params.get('medioPago') not in (None,payroll):
            raise ValueError('CD6_AGENCIA_NOMINA_NO_CONCORDANTE')
        context['sucursal']=branch;params['medioPago']=payroll
        result['perfilCore']={'sucursal':branch,'nomina':payroll,'tipoNomina':kind}
        cursor.execute(PERSONAL_IDENTITY_NAME_SQL,{'documento':document,'tercero':int(third)})
        names=cursor.fetchmany(2)
        if len(names)!=1 or not names[0][0] or not names[0][2]:raise ValueError('CD6_NOMBRE_INSTITUCIONAL_NO_ACREDITADO')
        name=' '.join(str(v).strip() for v in names[0] if v is not None and str(v).strip())
        customer=context['datosRadicacion']['cliente']
        if customer.get('nombre') not in (None,name):raise ValueError('CD6_NOMBRE_INSTITUCIONAL_NO_CONCORDANTE')
        customer['nombre']=name
        from .production_preflight import snapshot,require_personal_start_boundary
        observed=snapshot(cursor,follow_paths=True);require_personal_start_boundary(observed)
        result['preflight']=observed
    finally:
        if cursor is not None:cursor.close()
        connection.rollback();connection.close()
    return result
