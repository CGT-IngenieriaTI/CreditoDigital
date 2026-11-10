import hashlib
import random
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from apps.solicitudes.models import EstadoSolicitud
from apps.utils.logging import audit_event
from apps.xcore_consumo.models import CanalOtp, ConsentimientoConsumo, EstadoOtp, EstadoSolicitudConsumo, OtpChallenge
from apps.xcore_consumo.services.consent_pdf import build_consent_footer_pdf
from apps.xcore_consumo.services.otp_crypto import OTPCryptoError, decrypt_text, encrypt_text
from apps.xcore_consumo.services.orchestration import persist_orchestration_snapshot
from apps.xcore_consumo.services.twilio_verify import TwilioVerifyClient, TwilioVerifyError

PROVIDER_TWILIO_VERIFY = "twilio_verify"
PROVIDER_INTERNAL_EMAIL = "internal_email"
PROVIDER_TEST_INTERNAL = "test_internal"


def mask_destination(channel: str, destination: str) -> str:
    if channel == CanalOtp.EMAIL and "@" in destination:
        user, domain = destination.split("@", 1)
        return f"{user[:2]}***@{domain}"
    if len(destination) >= 4:
        return f"***{destination[-4:]}"
    return destination


def mask_otp(code: str) -> str:
    if not code:
        return "******"
    if len(code) <= 2:
        return "*" * len(code)
    return f"{code[:2]}{'*' * max(len(code) - 2, 1)}"


def normalize_sms_destination(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("57") and len(digits) == 12:
        digits = digits[2:]
    if not re.fullmatch(r"3\d{9}", digits):
        raise ValueError("El celular debe ser un n?mero m?vil colombiano v?lido.")
    return f"+57{digits}"


def generate_otp_code() -> str:
    return f"{random.randint(0, 999999):06d}"


def get_otp_mode() -> str:
    mode = str(getattr(settings, "OTP_PROVIDER_MODE", "test" if settings.DEBUG else "real")).strip().lower()
    return "test" if mode == "test" else "real"


def _request_meta(request) -> dict:
    if request is None:
        return {"ip_address": None, "user_agent": ""}
    ip_address = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", ""))
    if "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()
    return {
        "ip_address": ip_address or None,
        "user_agent": request.headers.get("User-Agent", "")[:255],
    }


def get_active_otp_challenge(solicitud):
    return solicitud.active_otp_challenge


def _otp_message(code: str) -> str:
    return (
        "Tu código de verificación para Crédito Digital Congente es: "
        f"{code}. Tiene vigencia de {max(1, int(getattr(settings, 'OTP_EMAIL_TTL_SECONDS', 600) / 60))} minutos."
    )


def _invalidate_pending_challenges(solicitud, *, keep_id=None):
    candidates = solicitud.otp_challenges.exclude(
        estado__in=[EstadoOtp.VALIDADA, EstadoOtp.EXPIRADA, EstadoOtp.BLOQUEADA, EstadoOtp.CANCELADA]
    )
    if keep_id:
        candidates = candidates.exclude(id=keep_id)
    candidates.update(estado=EstadoOtp.CANCELADA, validation_result="canceled", ultimo_error="Reemplazada por un nuevo envío OTP.")


def _create_base_challenge(solicitud, *, channel: str, provider: str, destination: str, request=None) -> OtpChallenge:
    meta = _request_meta(request)
    destination_masked = mask_destination(channel, destination)
    encrypted_destination = encrypt_text(destination)

    return OtpChallenge.objects.create(
        solicitud=solicitud,
        canal=channel,
        provider=provider,
        destino=destination_masked,
        destination_masked=destination_masked,
        destination_full_encrypted=encrypted_destination,
        estado=EstadoOtp.PENDIENTE,
        max_intentos=getattr(settings, "TWILIO_VERIFY_RESEND_MAX", 3),
        transaction_uuid=uuid.uuid4().hex,
        ip_address=meta["ip_address"],
        user_agent=meta["user_agent"],
    )


def _send_email(destination: str, code: str, solicitud):
    from_email = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "")).strip()
    if not from_email or not getattr(settings, "EMAIL_HOST", ""):
        raise ValueError("No hay un proveedor SMTP configurado para OTP.")

    html_body = render_to_string(
        "xcore_consumo/email_otp.html",
        {
            "logo_url": getattr(settings, "OTP_EMAIL_LOGO_URL", ""),
            "nombre_asociado": solicitud.solicitante.primer_apellido,
            "otp_code": code,
            "vigencia_minutos": max(1, int(getattr(settings, "OTP_EMAIL_TTL_SECONDS", 600) / 60)),
            "consentimiento_url": getattr(settings, "OTP_EMAIL_CONSENT_URL", ""),
        },
    )

    message = EmailMultiAlternatives(
        subject="Código OTP Crédito Digital Congente",
        body=_otp_message(code),
        from_email=from_email,
        to=[destination],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


def _persist_verified_consent(solicitud, challenge: OtpChallenge, *, actor: str, now):
    consentimiento = getattr(solicitud, "consentimiento_consumo", None)
    if not consentimiento or not consentimiento.aceptado:
        return

    text_snapshot = consentimiento.text_snapshot
    version = consentimiento.version
    consentimiento.otp = challenge
    consentimiento.firmado = True
    consentimiento.canal = challenge.canal
    consentimiento.text_hash = build_consent_hash(
        version,
        text_snapshot,
        challenge.transaction_uuid,
        challenge.canal,
        challenge.verificado_at,
    )
    consentimiento.tipo_firma = "ACEPTACION_OTP"
    consentimiento.evidencia = {
        **(consentimiento.evidencia or {}),
        "accepted_by": actor,
        "otp_verified_at": now.isoformat(),
        "otp_channel": challenge.canal,
        "provider": challenge.provider,
        "destination_masked": challenge.destination_masked or challenge.destino,
        "transaction_uuid": challenge.transaction_uuid,
        "verification_sid": challenge.verification_sid,
        "verification_check_sid": challenge.verification_check_sid,
    }
    pdf_bytes = build_consent_footer_pdf(
        channel=challenge.canal,
        destination_masked=challenge.destination_masked or challenge.destino,
        transaction_uuid=challenge.transaction_uuid,
    )
    consentimiento.pdf_consentimiento.save(
        f"{solicitud.numero_solicitud}_consentimiento.pdf",
        ContentFile(pdf_bytes),
        save=False,
    )
    consentimiento.save(
        update_fields=(
            "otp",
            "firmado",
            "canal",
            "text_hash",
            "tipo_firma",
            "evidencia",
            "pdf_consentimiento",
            "updated_at",
        )
    )


def send_otp(solicitud, *, channel: str, actor: str, request=None):
    consentimiento = getattr(solicitud, "consentimiento_consumo", None)
    if not consentimiento or not consentimiento.aceptado:
        raise ValueError("Debes aceptar términos y documentos antes de enviar el OTP.")

    now = timezone.now()
    current = get_active_otp_challenge(solicitud)
    cooldown = int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 30) or 30)
    if current and current.enviado_at and (now - current.enviado_at).total_seconds() < cooldown:
        raise ValueError("Debes esperar unos segundos antes de reenviar el OTP.")

    _invalidate_pending_challenges(solicitud)

    destination = normalize_sms_destination(solicitud.solicitante.celular) if channel == CanalOtp.SMS else solicitud.solicitante.email
    mode = get_otp_mode()

    if mode == "test":
        code = generate_otp_code()
        try:
            challenge = _create_base_challenge(
                solicitud,
                channel=channel,
                provider=PROVIDER_TEST_INTERNAL,
                destination=destination,
                request=request,
            )
            challenge.codigo = code
            challenge.otp_code_encrypted = encrypt_text(code)
            challenge.otp_hash = make_password(code)
            challenge.otp_masked = mask_otp(code)
            challenge.estado = EstadoOtp.ENVIADA
            challenge.enviado_at = now
            challenge.expira_at = now + timedelta(seconds=int(getattr(settings, "OTP_EMAIL_TTL_SECONDS", 600) or 600))
            challenge.save(
                update_fields=(
                    "codigo",
                    "otp_code_encrypted",
                    "otp_hash",
                    "otp_masked",
                    "estado",
                    "enviado_at",
                    "expira_at",
                    "updated_at",
                )
            )
        except OTPCryptoError as exc:
            raise ValueError(str(exc)) from exc
    elif channel == CanalOtp.SMS:
        challenge = None
        try:
            challenge = _create_base_challenge(
                solicitud,
                channel=channel,
                provider=PROVIDER_TWILIO_VERIFY,
                destination=destination,
                request=request,
            )
            verification = TwilioVerifyClient().start_verification(
                destination,
                channel=getattr(settings, "TWILIO_VERIFY_CHANNEL", "sms"),
                template_sid=getattr(settings, "TWILIO_VERIFY_TEMPLATE_SID", "") or None,
            )
        except (TwilioVerifyError, Exception) as exc:
            if challenge is not None:
                challenge.estado = EstadoOtp.ERROR_ENVIO
                challenge.validation_result = "send_error"
                challenge.ultimo_error = str(exc)
                challenge.save(update_fields=("estado", "validation_result", "ultimo_error", "updated_at"))
            raise ValueError(str(exc)) from exc
        challenge.estado = EstadoOtp.ENVIADA
        challenge.enviado_at = now
        challenge.expira_at = now + timedelta(seconds=int(getattr(settings, "TWILIO_VERIFY_TTL_SECONDS", 600) or 600))
        challenge.verification_sid = getattr(verification, "sid", "")
        challenge.save(update_fields=("estado", "enviado_at", "expira_at", "verification_sid", "updated_at"))
    else:
        code = generate_otp_code()
        challenge = None
        try:
            challenge = _create_base_challenge(
                solicitud,
                channel=channel,
                provider=PROVIDER_INTERNAL_EMAIL,
                destination=destination,
                request=request,
            )
            challenge.otp_code_encrypted = encrypt_text(code)
            challenge.otp_hash = make_password(code)
            challenge.otp_masked = mask_otp(code)
            _send_email(destination, code, solicitud)
        except (OTPCryptoError, Exception) as exc:
            if challenge is not None:
                challenge.estado = EstadoOtp.ERROR_ENVIO
                challenge.validation_result = "send_error"
                challenge.ultimo_error = str(exc)
                challenge.save(update_fields=("estado", "validation_result", "ultimo_error", "updated_at"))
            raise ValueError(f"No fue posible enviar la OTP por email: {exc}") from exc
        challenge.estado = EstadoOtp.ENVIADA
        challenge.enviado_at = now
        challenge.expira_at = now + timedelta(seconds=int(getattr(settings, "OTP_EMAIL_TTL_SECONDS", 600) or 600))
        challenge.save(
            update_fields=(
                "otp_code_encrypted",
                "otp_hash",
                "otp_masked",
                "estado",
                "enviado_at",
                "expira_at",
                "updated_at",
            )
        )

    detail = solicitud.consumo_detail
    detail.estado = EstadoSolicitudConsumo.OTP_PENDIENTE
    detail.ultimo_error = ""
    detail.save(update_fields=("estado", "ultimo_error", "updated_at"))
    solicitud.estado = EstadoSolicitud.OTP_PENDIENTE
    solicitud.paso_actual = "otp"
    solicitud.ultimo_error = ""
    solicitud.save(update_fields=("estado", "paso_actual", "ultimo_error", "updated_at"))

    audit_event(
        "consumo_otp_enviada",
        solicitud=solicitud,
        actor=actor,
        payload={
            "channel": channel,
            "provider": challenge.provider,
            "destination": challenge.destination_masked or challenge.destino,
            "expires_at": challenge.expira_at.isoformat() if challenge.expira_at else "",
            "otp_mode": mode,
            "transaction_uuid": challenge.transaction_uuid,
            "verification_sid": challenge.verification_sid,
        },
    )
    return challenge


def verify_otp(solicitud, *, code: str, actor: str, request=None):
    challenge = get_active_otp_challenge(solicitud)
    consentimiento = getattr(solicitud, "consentimiento_consumo", None)
    now = timezone.now()
    if challenge is None:
        raise ValueError("La solicitud no tiene OTP generada.")
    if challenge.estado == EstadoOtp.BLOQUEADA:
        raise ValueError("La OTP está bloqueada. Solicita un nuevo envío.")
    if not challenge.enviado_at or not challenge.expira_at:
        raise ValueError("Debes enviar la OTP antes de validarla.")
    if challenge.expira_at < now:
        challenge.estado = EstadoOtp.EXPIRADA
        challenge.validation_result = "expired"
        challenge.ultimo_error = "OTP expirada."
        challenge.save(update_fields=("estado", "validation_result", "ultimo_error", "updated_at"))
        raise ValueError("La OTP expiró. Solicita un nuevo envío.")

    challenge.intentos += 1
    approved = False

    if challenge.provider == PROVIDER_TWILIO_VERIFY:
        try:
            destination = decrypt_text(challenge.destination_full_encrypted)
            verification_check = TwilioVerifyClient().check_verification(destination, code)
        except (OTPCryptoError, TwilioVerifyError, Exception) as exc:
            challenge.validation_result = "verify_error"
            challenge.ultimo_error = str(exc)
            challenge.save(update_fields=("intentos", "validation_result", "ultimo_error", "updated_at"))
            raise ValueError(str(exc)) from exc
        challenge.verification_check_sid = getattr(verification_check, "sid", "")
        approved = str(getattr(verification_check, "status", "")).lower() == "approved"
    else:
        approved = bool(challenge.otp_hash) and check_password(code, challenge.otp_hash)

    if not approved:
        challenge.validation_result = "invalid_code"
        challenge.ultimo_error = "OTP incorrecta."
        if challenge.intentos >= challenge.max_intentos:
            challenge.estado = EstadoOtp.BLOQUEADA
            challenge.validation_result = "max_attempts_reached"
            challenge.ultimo_error = "Máximo de intentos alcanzado."
            challenge.blocked_until = now + timedelta(minutes=15)
            challenge.save(
                update_fields=(
                    "intentos",
                    "estado",
                    "validation_result",
                    "ultimo_error",
                    "blocked_until",
                    "verification_check_sid",
                    "updated_at",
                )
            )
            audit_event(
                "consumo_otp_bloqueada",
                solicitud=solicitud,
                actor=actor,
                payload={"attempts": challenge.intentos, "otp_mode": get_otp_mode(), "provider": challenge.provider},
            )
            raise ValueError("OTP incorrecta. Se alcanzó el número mÃ¡ximo de intentos.")
        challenge.save(
            update_fields=("intentos", "validation_result", "ultimo_error", "verification_check_sid", "updated_at")
        )
        raise ValueError("El código OTP es incorrecto.")

    challenge.estado = EstadoOtp.VALIDADA
    challenge.verificado_at = now
    challenge.validation_result = "approved"
    challenge.ultimo_error = ""
    challenge.save(
        update_fields=(
            "intentos",
            "estado",
            "verificado_at",
            "validation_result",
            "ultimo_error",
            "verification_check_sid",
            "updated_at",
        )
    )
    _invalidate_pending_challenges(solicitud, keep_id=challenge.id)

    detail = solicitud.consumo_detail
    if consentimiento and consentimiento.aceptado:
        detail = persist_orchestration_snapshot(detail)
        _persist_verified_consent(solicitud, challenge, actor=actor, now=now)
        detail.estado = EstadoSolicitudConsumo.CONSENTIMIENTO_FIRMADO
        solicitud.estado = EstadoSolicitud.CONSENTIMIENTO_FIRMADO
        solicitud.paso_actual = "analisis"
    else:
        detail.estado = EstadoSolicitudConsumo.OTP_VALIDADA
        solicitud.estado = EstadoSolicitud.OTP_VALIDADA
        solicitud.paso_actual = "consentimiento"
    detail.ultimo_error = ""
    detail.save(update_fields=("estado", "ultimo_error", "updated_at"))
    solicitud.ultimo_error = ""
    solicitud.save(update_fields=("estado", "paso_actual", "ultimo_error", "updated_at"))

    audit_event(
        "consumo_otp_validada",
        solicitud=solicitud,
        actor=actor,
        payload={
            "channel": challenge.canal,
            "provider": challenge.provider,
            "verified_at": now.isoformat(),
            "otp_mode": get_otp_mode(),
            "transaction_uuid": challenge.transaction_uuid,
            "verification_sid": challenge.verification_sid,
            "verification_check_sid": challenge.verification_check_sid,
        },
    )
    return challenge


def build_consent_hash(version: str, text_snapshot: str, transaction_uuid: str = "", channel: str = "", verified_at=None) -> str:
    verified_value = verified_at.isoformat() if verified_at else ""
    source = f"{version}|{text_snapshot}|{transaction_uuid}|{channel}|{verified_value}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()

