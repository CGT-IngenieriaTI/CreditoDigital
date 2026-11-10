from dataclasses import dataclass, field


@dataclass(frozen=True)
class CreditModalityConfig:
    code: str
    name: str
    valid_destinations: tuple[str, ...] = ()


@dataclass(frozen=True)
class CreditLineConfig:
    key: str
    product_code: str
    workflow_model: str
    transport: str
    workflow_process: str = "CREDITOS"
    workflow_application_code: str = "19"
    radication_activity_id: int | None = None
    approval_activity_id: int | None = None
    required_disbursement: bool = True
    required_pickups_structure: bool = True
    required_codebtors_structure: bool = True
    modalities: dict[str, CreditModalityConfig] = field(default_factory=dict)

    def get_modality(self, code: str | None) -> CreditModalityConfig | None:
        normalized = str(code or "").strip().upper()
        if not normalized:
            return None
        return self.modalities.get(normalized)


MICROCREDITO_CONFIG = CreditLineConfig(
    key="MICROCREDITO",
    product_code="MICROCREDITO",
    workflow_model="MICROCREDITO",
    transport="microcredito_interface",
)


LIBRE_INVERSION_CONFIG = CreditLineConfig(
    key="LIBRE_INVERSION",
    product_code="300",
    workflow_model="SOLICITUD_LIBRE_INVERSION",
    transport="workflow_credit_documented",
    radication_activity_id=113,
    approval_activity_id=6774,
    modalities={
        "127": CreditModalityConfig(
            code="127",
            name="Libre Inversion - Credito Digital",
            valid_destinations=("PSC",),
        ),
    },
)


CONSUMO_CONFIG = CreditLineConfig(
    key="CONSUMO",
    product_code="CONSUMO",
    workflow_model="SOLICITUD_DE_CREDITOS",
    transport="workflow_credit_start_only",
)


ROTATIVO_CONFIG = CreditLineConfig(
    key="ROTATIVO",
    product_code="360",
    workflow_model="SOLICITUD_CREDITO_ROTATIVO",
    transport="workflow_credit_start_only",
)


CESION_CDAT_CONFIG = CreditLineConfig(
    key="CESION_CDAT",
    product_code="380",
    workflow_model="SOLICITUD_CESION_CDAT",
    transport="workflow_credit_start_only",
)


CREDIHOY_DIGITAL_CONFIG = CreditLineConfig(
    key="CREDIHOY_DIGITAL",
    product_code="390",
    workflow_model="CREDIHOY_VAL_EMPLEADO_DIGITAL",
    transport="credihoy_digital_controlled",
    radication_activity_id=113,
    approval_activity_id=6774,
    required_disbursement=False,
    required_pickups_structure=False,
    required_codebtors_structure=False,
    modalities={
        "111": CreditModalityConfig(
            code="111",
            name="Credihoy",
            valid_destinations=("CFA", "390"),
        ),
    },
)


CREDIT_LINE_ALIASES = {
    "MICROCREDITO": MICROCREDITO_CONFIG,
    "CONSUMO": CONSUMO_CONFIG,
    "SOLICITUD_DE_CREDITOS": CONSUMO_CONFIG,
    "LIBRE_INVERSION": LIBRE_INVERSION_CONFIG,
    "LIBRE INVERSION": LIBRE_INVERSION_CONFIG,
    "SOLICITUD_LIBRE_INVERSION": LIBRE_INVERSION_CONFIG,
    "300": LIBRE_INVERSION_CONFIG,
    "ROTATIVO": ROTATIVO_CONFIG,
    "SOLICITUD_CREDITO_ROTATIVO": ROTATIVO_CONFIG,
    "360": ROTATIVO_CONFIG,
    "CESION_CDAT": CESION_CDAT_CONFIG,
    "SOLICITUD_CESION_CDAT": CESION_CDAT_CONFIG,
    "380": CESION_CDAT_CONFIG,
    "CREDIHOY_DIGITAL": CREDIHOY_DIGITAL_CONFIG,
    "CREDIHOY VAL EMPLEADO DIGITAL": CREDIHOY_DIGITAL_CONFIG,
    "CREDIHOY_VAL_EMPLEADO_DIGITAL": CREDIHOY_DIGITAL_CONFIG,
}


def resolve_credit_line_config(modelo_solicitado: str | None) -> CreditLineConfig | None:
    normalized = str(modelo_solicitado or "").strip().upper()
    if not normalized:
        return MICROCREDITO_CONFIG
    return CREDIT_LINE_ALIASES.get(normalized)
