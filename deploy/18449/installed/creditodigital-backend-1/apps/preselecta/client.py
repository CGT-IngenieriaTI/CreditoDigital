import base64
import logging
import os
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache


class PreselectaClientError(Exception):
    pass


logger = logging.getLogger("credito")


class PreselectaClient:
    def __init__(self):
        self.timeout = settings.EXTERNAL_API_TIMEOUT
        self.verify_ssl = str(getattr(settings, "PRESELECTA_VERIFY_SSL", "1")).lower() not in {
            "0",
            "false",
        }
        self.auth_style = str(getattr(settings, "PRESELECTA_AUTH_STYLE", "access_token")).lower()
        self.grant_type = str(getattr(settings, "OKTA_GRANT_TYPE", "password")).lower()
        self.token_url = self._required("OKTA_TOKEN_URL")
        self.client_id = self._required("OKTA_CLIENT_ID")
        self.client_secret = self._required("OKTA_CLIENT_SECRET")
        self.username = self._optional("OKTA_USERNAME")
        self.password = self._optional("OKTA_PASSWORD")
        self.scope = self._required("OKTA_SCOPE")
        self.service_url = self._required("SERVICE_URL")
        self.inquiry_id = self._optional("PRESELECTA_INQUIRY_ID", getattr(settings, "PRESELECTA_INQUIRY_ID", "892000373"))
        self.inquiry_client_type = self._optional(
            "PRESELECTA_INQUIRY_CLIENT_TYPE",
            getattr(settings, "PRESELECTA_INQUIRY_CLIENT_TYPE", "2"),
        )
        self.inquiry_user_type = self._optional(
            "PRESELECTA_INQUIRY_USER_TYPE",
            getattr(settings, "PRESELECTA_INQUIRY_USER_TYPE", "2"),
        )

    def _required(self, key: str) -> str:
        value = str(getattr(settings, key, "") or os.getenv(key, "")).strip()
        if value:
            return value
        raise PreselectaClientError(f"Falta configurar {key}")

    def _optional(self, key: str, default: str = "") -> str:
        return str(getattr(settings, key, "") or os.getenv(key, default) or default).strip()

    def _basic_header(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return f"Basic {base64.b64encode(raw).decode('utf-8')}"

    def get_access_token(self) -> str:
        cache_key = f"preselecta_access_token_{self.client_id}_{self.grant_type}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        headers = {
            "Authorization": self._basic_header(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        if self.grant_type == "client_credentials":
            data = {"grant_type": "client_credentials", "scope": self.scope}
        else:
            data = {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
                "scope": self.scope,
            }
        try:
            response = requests.post(
                self.token_url,
                headers=headers,
                data=data,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise PreselectaClientError("No fue posible obtener token de PRESELECTA.") from exc
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise PreselectaClientError("La respuesta de Okta no incluyo access_token.")
        expires_in = int(payload.get("expires_in", 3600))
        cache.set(cache_key, token, timeout=max(60, expires_in - 60))
        return token

    def _build_service_headers(self, token: str) -> dict:
        headers = {
            "Content-Type": "application/json",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.auth_style == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["access_token"] = token
        return headers

    def evaluate(self, payload: dict) -> dict:
        normalized_payload = self._normalize_service_payload(payload)
        if settings.CREDIT_USE_MOCK_SERVICES:
            mocked = self._mock_response(normalized_payload)
            return mocked
        token = self.get_access_token()
        try:
            response = requests.post(
                self.service_url,
                headers=self._build_service_headers(token),
                json=normalized_payload,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            detail = self._extract_http_error_detail(exc)
            logger.error("preselecta.http_error message=%s status=%s", detail.get("message"), detail.get("status_code"))
            raise PreselectaClientError(detail.get("message") or "No fue posible consultar PRESELECTA.") from exc
        return self._normalize_provider_response(data, normalized_payload)

    def _normalize_service_payload(self, payload: dict) -> dict:
        if "inquiryParameters" in payload:
            normalized = dict(payload)
            normalized["inquiryClientId"] = payload.get("inquiryClientId") or self.inquiry_id
            normalized["inquiryClientType"] = payload.get("inquiryClientType") or self.inquiry_client_type
            normalized["inquiryUserId"] = payload.get("inquiryUserId") or self.inquiry_id
            normalized["inquiryUserType"] = payload.get("inquiryUserType") or self.inquiry_user_type
            return normalized
        return {
            "idNumber": payload.get("idNumber") or payload.get("numero_identificacion"),
            "idType": payload.get("idType", "1"),
            "firstLastName": payload.get("firstLastName") or payload.get("primer_apellido", ""),
            "provider_test_identity": bool(payload.get("provider_test_identity")),
            "provider_test_case": payload.get("provider_test_case", ""),
            "inquiryClientId": payload.get("inquiryClientId") or self.inquiry_id,
            "inquiryClientType": payload.get("inquiryClientType") or self.inquiry_client_type,
            "inquiryUserId": payload.get("inquiryUserId") or self.inquiry_id,
            "inquiryUserType": payload.get("inquiryUserType") or self.inquiry_user_type,
            "inquiryParameters": [
                {"paramType": "STRAID", "keyvalue": {"key": "T", "value": "25674"}},
                {"paramType": "STRNAM", "keyvalue": {"key": "T", "value": "PRECREDITO_CONGENTE"}},
                {
                    "paramType": "LINEA_CREDITO",
                    "keyvalue": {"key": "T", "value": str(payload.get("linea_credito", "1"))},
                },
                {
                    "paramType": "TIPO_ASOCIADO",
                    "keyvalue": {"key": "T", "value": str(payload.get("tipo_asociado", "1"))},
                },
                {
                    "paramType": "MEDIO_PAGO",
                    "keyvalue": {"key": "T", "value": str(payload.get("medio_pago", "1"))},
                },
                {
                    "paramType": "ACTIVIDAD",
                    "keyvalue": {"key": "T", "value": str(payload.get("actividad", "1"))},
                },
            ],
        }

    def _normalize_provider_response(self, data: dict[str, Any], normalized_payload: dict[str, Any]) -> dict[str, Any]:
        fault = data.get("Fault") if isinstance(data, dict) and isinstance(data.get("Fault"), dict) else {}
        engine_response = data.get("engineResponse", []) if isinstance(data, dict) else []
        decision = self._extract_engine_value(data, "DECISION")
        risk_level = self._extract_engine_value(data, "RIESGO_SCORE")
        score = self._extract_score(data)
        combined = f"{decision} {risk_level}".upper()
        preaprobado = decision.upper() == "APROBADO" or ("ZONA" in combined and "GRIS" in combined)
        mensaje = (
            fault.get("faultstring")
            or data.get("message")
            or data.get("mensaje")
            or decision
            or risk_level
            or "Consulta PRESELECTA realizada."
        )
        estado = data.get("typeResponse") or data.get("estado") or ("ERROR" if fault else "OK")
        return {
            "estado": estado,
            "mensaje": mensaje,
            "mensaje_tecnico": fault.get("faultstring") or "",
            "preaprobado": preaprobado,
            "score": score if score not in (None, "") else "",
            "decision": decision,
            "risk_level": risk_level,
            "engine_response": engine_response if isinstance(engine_response, list) else [],
            "fault": fault,
            "raw": data,
            "request_payload": normalized_payload,
            "identity_effective": {
                "numero_identificacion": normalized_payload.get("idNumber", ""),
                "tipo_identificacion": normalized_payload.get("idType", ""),
                "primer_apellido": normalized_payload.get("firstLastName", ""),
                "provider_test_identity": bool(normalized_payload.get("provider_test_identity")),
                "provider_test_case": normalized_payload.get("provider_test_case", ""),
            },
        }

    def _extract_engine_value(self, payload: dict[str, Any], key: str) -> str:
        engine = payload.get("engineResponse", []) if isinstance(payload, dict) else []
        key_lower = key.lower()
        for item in engine:
            if str(item.get("key", "")).lower() == key_lower:
                return str(item.get("value", "")).strip()
        return ""

    def _extract_score(self, payload: dict[str, Any]) -> float | str:
        score_obj = payload.get("score") if isinstance(payload, dict) else None
        candidates = [
            score_obj.get("rating") if isinstance(score_obj, dict) else score_obj,
            payload.get("score") if isinstance(payload, dict) else None,
            payload.get("resultado", {}).get("score") if isinstance(payload.get("resultado"), dict) else None,
            payload.get("data", {}).get("score") if isinstance(payload.get("data"), dict) else None,
            self._extract_engine_value(payload, "SCORE"),
        ]
        for candidate in candidates:
            if candidate not in (None, ""):
                try:
                    return float(candidate)
                except (TypeError, ValueError):
                    continue
        return ""

    def _extract_http_error_detail(self, exc: requests.RequestException) -> dict[str, Any]:
        response = getattr(exc, "response", None)
        if response is None:
            return {"message": "No fue posible consultar PRESELECTA.", "raw": str(exc)}
        try:
            payload = response.json()
        except ValueError:
            payload = {"detail": response.text}
        fault = payload.get("Fault") if isinstance(payload, dict) and isinstance(payload.get("Fault"), dict) else {}
        return {
            "message": fault.get("faultstring") or payload.get("detail") or str(exc),
            "raw": payload,
            "status_code": response.status_code,
        }

    def _mock_response(self, payload: dict) -> dict:
        raw_number = str(payload.get("idNumber") or payload.get("numero_identificacion") or "")
        digits = [int(char) for char in raw_number if char.isdigit()]
        last_digit = digits[-1] if digits else 0
        score = 640 + (last_digit * 25)
        decision = "APROBADO" if score >= 700 else "RECHAZADO"
        risk = "VERDE" if score >= 700 else "ROJO"
        raw = {
            "typeResponse": "SUCCESS",
            "engineResponse": [
                {"key": "DECISION", "value": decision},
                {"key": "RIESGO_SCORE", "value": risk},
            ],
            "score": {"rating": score},
            "message": (
                "Cliente preseleccionado para continuar al motor principal."
                if score >= 700
                else "Cliente no preseleccionado por politica inicial."
            ),
        }
        return self._normalize_provider_response(raw, payload)
