from django.conf import settings


import re


class TwilioVerifyError(Exception):
    pass


class TwilioVerifyClient:
    def __init__(self, *, service_sid: str | None = None):
        account_sid = str(getattr(settings, "TWILIO_ACCOUNT_SID", "") or "").strip()
        auth_token = str(getattr(settings, "TWILIO_AUTH_TOKEN", "") or "").strip()
        key_sid = str(getattr(settings, "TWILIO_VERIFY_API_KEY_SID", "") or "").strip()
        key_secret = str(getattr(settings, "TWILIO_VERIFY_API_KEY_SECRET", "") or "").strip()
        verify_sid = str(service_sid if service_sid is not None else getattr(settings, "TWILIO_VERIFY_SID", "") or "").strip()
        if key_sid or key_secret:
            if (not re.fullmatch(r"SK[a-fA-F0-9]{32}", key_sid) or len(key_secret) < 32
                    or not re.fullmatch(r"AC[a-fA-F0-9]{32}", account_sid) or not verify_sid):
                raise TwilioVerifyError("Credencial dedicada Verify incompleta.")
        elif not account_sid or not auth_token or not verify_sid:
            raise TwilioVerifyError("Twilio Verify no está configurado completamente.")
        try:
            from twilio.rest import Client
        except Exception as exc:  # pragma: no cover
            raise TwilioVerifyError("La dependencia 'twilio' no está disponible en el entorno.") from exc
        self.verify_sid = verify_sid
        self.client = (Client(key_sid, key_secret, account_sid=account_sid)
                       if key_sid else Client(account_sid, auth_token))

    def start_verification(self, to_number: str, channel: str = "sms", template_sid: str | None = None):
        params = {"to": to_number, "channel": channel}
        if template_sid:
            params["template_sid"] = template_sid
        return self.client.verify.v2.services(self.verify_sid).verifications.create(**params)

    def check_verification(self, to_number: str, code: str, *, verification_sid: str | None = None):
        return (
            self.client.verify.v2.services(self.verify_sid)
            .verification_checks.create(**({'verification_sid': verification_sid, 'code': code}
                if verification_sid else {'to': to_number, 'code': code}))
        )

