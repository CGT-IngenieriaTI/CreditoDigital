from apps.solicitudes.models import EstadoSolicitud
from apps.xcore_consumo.models import EstadoOtp, EstadoSolicitudConsumo


def sync_solicitud_state(solicitud, *, estado: str, paso_actual: str, ultimo_error: str = ""):
    solicitud.estado = estado
    solicitud.paso_actual = paso_actual
    solicitud.ultimo_error = ultimo_error
    solicitud.save(update_fields=("estado", "paso_actual", "ultimo_error", "updated_at"))


def sync_consumo_state(detail, *, estado: str, ultimo_error: str = ""):
    detail.estado = estado
    detail.ultimo_error = ultimo_error
    detail.save(update_fields=("estado", "ultimo_error", "updated_at"))


def assert_otp_verified(solicitud):
    challenge = solicitud.active_otp_challenge
    if challenge is None:
        raise ValueError("La solicitud no tiene OTP generada.")
    if challenge.estado != EstadoOtp.VALIDADA:
        raise ValueError("Debes validar la OTP antes de continuar.")


def assert_consent_granted(solicitud):
    if not hasattr(solicitud, "consentimiento_consumo"):
        raise ValueError("Debes registrar el consentimiento antes de continuar.")
    if not solicitud.consentimiento_consumo.firmado:
        raise ValueError("El consentimiento aún no está firmado.")


def get_solicitud_state_for_consumo(estado_consumo: str) -> str:
    mapping = {
        EstadoSolicitudConsumo.OTP_PENDIENTE: EstadoSolicitud.OTP_PENDIENTE,
        EstadoSolicitudConsumo.OTP_VALIDADA: EstadoSolicitud.OTP_VALIDADA,
        EstadoSolicitudConsumo.CONSENTIMIENTO_PENDIENTE: EstadoSolicitud.CONSENTIMIENTO_PENDIENTE,
        EstadoSolicitudConsumo.CONSENTIMIENTO_FIRMADO: EstadoSolicitud.CONSENTIMIENTO_FIRMADO,
        EstadoSolicitudConsumo.CORE_CONSULTADO: EstadoSolicitud.CORE_CONSULTADO,
        EstadoSolicitudConsumo.FORMULARIO_XCORE_OK: EstadoSolicitud.FORMULARIO_XCORE_OK,
        EstadoSolicitudConsumo.PROCESANDO: EstadoSolicitud.PROCESANDO,
        EstadoSolicitudConsumo.FINALIZADO: EstadoSolicitud.FINALIZADA,
        EstadoSolicitudConsumo.ERROR: EstadoSolicitud.ERROR,
    }
    return mapping.get(estado_consumo, EstadoSolicitud.INICIADA)
