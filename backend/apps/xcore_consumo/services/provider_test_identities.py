import os


TEST_IDENTITY_CASES = {
    "VARGAS": {
        "tipo_identificacion": "1",
        "numero_identificacion": "1110501568",
        "primer_apellido": "VARGAS",
        "linea_credito": "1",
        "tipo_asociado": "1",
        "medio_pago": "1",
        "actividad": "1",
    },
    "GARCIA": {
        "tipo_identificacion": "1",
        "numero_identificacion": "24234676",
        "primer_apellido": "GARCIA",
        "linea_credito": "1",
        "tipo_asociado": "1",
        "medio_pago": "1",
        "actividad": "1",
    },
    "GOMEZ": {
        "tipo_identificacion": "1",
        "numero_identificacion": "1090438586",
        "primer_apellido": "GOMEZ",
        "linea_credito": "1",
        "tipo_asociado": "1",
        "medio_pago": "1",
        "actividad": "1",
    },
    "DUQUE": {
        "tipo_identificacion": "1",
        "numero_identificacion": "25096152",
        "primer_apellido": "DUQUE",
        "linea_credito": "1",
        "tipo_asociado": "1",
        "medio_pago": "1",
        "actividad": "1",
    },
}


def get_provider_mode() -> str:
    mode = str(os.getenv("XCORE_PROVIDER_MODE", "real")).strip().lower()
    return "test" if mode == "test" else "real"


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
