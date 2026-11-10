import requests
from django.conf import settings


class XcoreClientError(Exception):
    pass


class XcoreClient:
    def __init__(self):
        self.endpoint = settings.XCORE_API_URL
        self.timeout = settings.EXTERNAL_API_TIMEOUT

    def evaluate(self, payload: dict) -> dict:
        if settings.CREDIT_USE_MOCK_SERVICES:
            return self._mock_response(payload)
        try:
            response = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise XcoreClientError("No fue posible consultar XCORE.") from exc

    def _mock_response(self, payload: dict) -> dict:
        pre_score = payload["preselecta"]["score"]
        pay_score = payload["historial_pago"]["score_pago"]
        mora = payload["historial_pago"]["mora_maxima"]
        blended_score = round((pre_score * 0.45) + (pay_score * 0.55))

        if blended_score >= 760 and mora <= 5:
            return {
                "estado": "OK",
                "resultado": "APROBADO",
                "mensaje": "Credito aprobado por politica automatizada.",
                "monto_aprobado": 12000000,
                "plazo_aprobado": 36,
                "tasa_interes": "16.80",
                "detalle": {"score_final": blended_score, "mora_maxima": mora},
            }
        if blended_score < 690 or mora > 30:
            return {
                "estado": "OK",
                "resultado": "RECHAZADO",
                "mensaje": "Solicitud rechazada por politicas de riesgo.",
                "detalle": {"score_final": blended_score, "mora_maxima": mora},
            }
        return {
            "estado": "OK",
            "resultado": "REVISION",
            "mensaje": "Solicitud enviada a revision manual.",
            "detalle": {"score_final": blended_score, "mora_maxima": mora},
        }
