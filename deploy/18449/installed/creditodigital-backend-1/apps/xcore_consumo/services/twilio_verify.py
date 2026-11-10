from django.conf import settings


class TwilioVerifyError(Exception):
    pass


class TwilioVerifyClient:
    def __init__(self):
        account_sid = str(getattr(settings, "TWILIO_ACCOUNT_SID", "") or "").strip()
        auth_token = str(getattr(settings, "TWILIO_AUTH_TOKEN", "") or "").strip()
        verify_sid = str(getattr(settings, "TWILIO_VERIFY_SID", "") or "").strip()
        if not account_sid or not auth_token or not verify_sid:
            raise TwilioVerifyError("Twilio Verify no está configurado completamente.")
        try:
            from twilio.rest import Client
        except Exception as exc:  # pragma: no cover
            raise TwilioVerifyError("La dependencia 'twilio' no está disponible en el entorno.") from exc
        self.verify_sid = verify_sid
        self.client = Client(account_sid, auth_token)

    def start_verification(self, to_number: str, channel: str = "sms", template_sid: str | None = None):
        params = {"to": to_number, "channel": channel}
        if template_sid:
            params["template_sid"] = template_sid
        return self.client.verify.v2.services(self.verify_sid).verifications.create(**params)

    def check_verification(self, to_number: str, code: str):
        return (
            self.client.verify.v2.services(self.verify_sid)
            .verification_checks.create(to=to_number, code=code)
        )

