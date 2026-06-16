import os


TEST_IDENTITY_CASES = {
    "ORTIZ": {
        "tipo_identificacion": "1",
        "numero_identificacion": "1006442329",
        "primer_apellido": "ORTIZ",
        "linea_credito": "1",
        "tipo_asociado": "1",
        "medio_pago": "1",
        "actividad": "1",
    },
    "PALACIOS": {
        "tipo_identificacion": "1",
        "numero_identificacion": "86080032",
        "primer_apellido": "PALACIOS",
        "linea_credito": "1",
        "tipo_asociado": "1",
        "medio_pago": "1",
        "actividad": "1",
    },
}


def get_provider_mode() -> str:
    mode = str(os.getenv("XCORE_PROVIDER_MODE", "")).strip().lower()
    legacy_enabled = str(os.getenv("XCORE_USE_PROVIDER_TEST_IDENTITIES", "")).strip().lower()
    if mode == "test" or legacy_enabled in {"1", "true", "yes", "on"}:
        return "test"
    return "real"


def use_provider_test_identity() -> bool:
    if get_provider_mode() != "test":
        return False
    case_name = str(os.getenv("XCORE_PROVIDER_TEST_CASE", "")).strip().upper()
    return bool(case_name and case_name in TEST_IDENTITY_CASES)


def get_provider_test_case() -> str:
    case_name = str(os.getenv("XCORE_PROVIDER_TEST_CASE", "")).strip().upper()
    return case_name if case_name in TEST_IDENTITY_CASES else ""


def get_provider_test_identity() -> dict:
    case_name = get_provider_test_case()
    if not case_name:
        raise ValueError("XCORE_PROVIDER_TEST_CASE no es valido para modo pruebas.")
    return dict(TEST_IDENTITY_CASES[case_name])
