import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings


class LinixWebSessionError(RuntimeError):
    pass


class LinixWebExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class LinixWebResponse:
    status_code: int
    body: str

    @property
    def is_site_invalid(self):
        return "El sitio que est" in self.body and "no es v" in self.body


class LinixWebSessionClient:
    def __init__(self, php_session_id=None, nit=None, base_url=None, user_agent=None):
        self.base_url = (base_url or settings.LINIX_WEB_BASE_URL).rstrip("/")
        self.php_session_id = php_session_id or settings.LINIX_WEB_PHPSESSID
        self.nit = nit or settings.LINIX_WEB_NIT
        self.user_agent = user_agent or settings.LINIX_WEB_USER_AGENT
        if not self.php_session_id:
            raise ValueError("LINIX PHPSESSID es obligatorio")
        if not self.nit:
            raise ValueError("LINIX nit de sesion es obligatorio")

        self._context = ssl._create_unverified_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._context)
        )

    @property
    def execution_url(self):
        return f"{self.base_url}/lnxSeccion.php"

    @property
    def funcionario_url(self):
        return f"{self.execution_url}?objeto=gr_presentacion_Funcionario"

    @property
    def _cookie_header(self):
        return f"PHPSESSID={self.php_session_id}; nit={self.nit}"

    def _headers(self, accept="text/html, application/xml, text/xml, */*"):
        return {
            "Accept": accept,
            "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Cookie": self._cookie_header,
            "Origin": self.base_url,
            "Referer": f"{self.execution_url}?objeto=gr_presentacion_Funcionario",
            "User-Agent": self.user_agent,
            "X-Requested-With": "XMLHttpRequest",
        }

    def post(self, data, accept="text/html, application/xml, text/xml, */*"):
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")
        request = urllib.request.Request(
            self.execution_url,
            data=encoded_data,
            headers=self._headers(accept=accept),
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return LinixWebResponse(
                    status_code=response.status,
                    body=raw.decode(charset, errors="replace"),
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return LinixWebResponse(status_code=exc.code, body=body)

    def validate_session(self):
        request = urllib.request.Request(
            self.funcionario_url,
            headers=self._headers(),
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                linix_response = LinixWebResponse(
                    status_code=response.status,
                    body=raw.decode(charset, errors="replace"),
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            linix_response = LinixWebResponse(status_code=exc.code, body=body)

        self._raise_for_session(linix_response)
        if linix_response.status_code != 200:
            raise LinixWebSessionError(
                f"No fue posible validar sesion LINIX. HTTP {linix_response.status_code}"
            )
        return linix_response

    def build_load_execution_form_payload(
        self,
        flow_id,
        serie_id,
        options=(),
        capture_flag="N",
        authorization_flag="N",
        procedure="",
    ):
        data = {
            "objeto": "wf_tareas_Ejecutar",
            "metodo": "FormularioEjecucion",
            "servicio": "Y",
            "v_k_flujo": str(flow_id),
            "v_k_serie": str(serie_id),
            "n_i_captur": capture_flag,
            "n_i_autori": authorization_flag,
            "en_modal": "Y",
            "n_k_proced": procedure,
        }
        for index, option in enumerate(options, start=1):
            data[f"n_option{index}"] = option
        return data

    def load_execution_form(
        self,
        flow_id,
        serie_id,
        options=(),
        capture_flag="N",
        authorization_flag="N",
        procedure="",
    ):
        data = self.build_load_execution_form_payload(
            flow_id,
            serie_id,
            options=options,
            capture_flag=capture_flag,
            authorization_flag=authorization_flag,
            procedure=procedure,
        )
        response = self.post(data)
        self._raise_for_session(response)
        if response.status_code != 200:
            raise LinixWebExecutionError(
                f"LINIX no devolvio formulario valido. HTTP {response.status_code}: "
                f"{response.body[:300]}"
            )
        return response

    def build_execute_task_payload(
        self,
        flow_id,
        serie_id,
        response_value=None,
        observation=None,
        auth_date=None,
        extra_fields=None,
    ):
        data = {
            "objeto": "wf_tareas_Ejecutar",
            "metodo": "EjecutaTarea",
            "servicio": "Y",
            "v_k_flujo": str(flow_id),
            "v_k_serie": str(serie_id),
        }
        if auth_date is not None:
            data["F_AUTORI"] = auth_date
        if observation is not None:
            data["N_OBSERVACION"] = observation
        if response_value is not None:
            data["A_CODIGO_RESPUESTA"] = response_value
            data["n_respue"] = response_value
        if extra_fields:
            data.update(extra_fields)
        return data

    def execute_task(
        self,
        flow_id,
        serie_id,
        response_value=None,
        observation=None,
        auth_date=None,
        extra_fields=None,
    ):
        data = self.build_execute_task_payload(
            flow_id,
            serie_id,
            response_value=response_value,
            observation=observation,
            auth_date=auth_date,
            extra_fields=extra_fields,
        )
        response = self.post(data)
        self._raise_for_session(response)
        if "La tarea fue ejecutada" not in response.body:
            raise LinixWebExecutionError(
                f"LINIX no confirmo ejecucion exitosa. HTTP {response.status_code}: "
                f"{response.body[:500]}"
            )
        return response

    def get_next_route_permission(self, flow_id):
        data = {
            "objeto": "wf_tareas_Ruta",
            "metodo": "PermisoSiguienteRuta",
            "servicio": "Y",
            "valorMetodo": "Y",
            "v_k_flujo": str(flow_id),
        }
        response = self.post(data, accept="application/json")
        self._raise_for_session(response)
        if response.status_code != 200:
            raise LinixWebExecutionError(
                f"LINIX no devolvio permiso de siguiente ruta. HTTP {response.status_code}: "
                f"{response.body[:300]}"
            )
        return response

    @staticmethod
    def _raise_for_session(response):
        if response.is_site_invalid:
            raise LinixWebSessionError(
                "Sesion LINIX invalida o expirada: la capa web respondio sitio no valido"
            )
