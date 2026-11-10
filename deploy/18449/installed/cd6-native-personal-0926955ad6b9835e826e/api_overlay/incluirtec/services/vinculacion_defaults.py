from django.conf import settings


def _setting(name, default):
    return getattr(settings, name, default)


AFFILIATION_FIXED_DEFAULTS = {
    "codigoPromotor": "0",
    "nomina": "999",
    "asocon": "1",
    "cencos": "999",
    "unidadEjecucion": "100",
    "codigoConcepto": "101",
    "cargo": "99901",
    "codigoCargo": "99901",
    "dependencia": "99901",
    "medioPago": "999",
}


SAVINGS_PRODUCT_DEFAULTS = {
    "102": {
        "clasificacion": "102",
        "producto": "232",
        "nombre": "AHORRA FACIL",
        "titularidad": "IND",
        "pagoAutomatico": "Y",
        "gmfSubsidiado": "N",
        "pagoNomina": "Y",
        "gmf": "Y",
        "combinaFirma": "TIT",
        "combinaSello": "NR",
    }
}


def affiliation_defaults():
    defaults = dict(AFFILIATION_FIXED_DEFAULTS)
    defaults.update(
        {
            "codigoPromotor": _setting("VINCULACION_PROMOTOR_DEFAULT", "0"),
            "seccion": _setting("VINCULACION_SECCION_DEFAULT", "U"),
            "actividadCiiuSecundaria": _setting(
                "VINCULACION_ACTIVIDAD_CIIU_SECUNDARIA_DEFAULT",
                "0121",
            ),
        }
    )
    return defaults


def savings_product_defaults(product_code=None):
    code = str(product_code or _setting("VINCULACION_PRODUCTO_AHORROS_DEFAULT", "102")).strip()
    try:
        return dict(SAVINGS_PRODUCT_DEFAULTS[code])
    except KeyError:
        raise ValueError(f"Producto de cuenta de ahorros no soportado: {code}")
