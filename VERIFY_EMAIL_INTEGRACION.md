# Integracion Twilio Verify + Email SMTP

Este documento resume la integracion actual del proyecto para:

- OTP por SMS usando **Twilio Verify**
- OTP por EMAIL usando **SMTP**

## Regla de seguridad

Las credenciales actuales **no se duplican** en este documento para evitar dejar
secretos repetidos en otro archivo del repositorio.

La fuente unica de credenciales vigentes es:

- [.env](C:\.vscode\Preselecta\.env)

## 1. Twilio Verify

### Archivo fuente actual

- [twilio_verify.py](C:\.vscode\Preselecta\integrations\services\twilio_verify.py)

### Variables requeridas

Tomar desde [.env](C:\.vscode\Preselecta\.env):

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_VERIFY_SID`
- `TWILIO_VERIFY_CHANNEL`
- `TWILIO_VERIFY_TEMPLATE_SID`
- `TWILIO_VERIFY_TTL_SECONDS`
- `TWILIO_VERIFY_RESEND_COOLDOWN`
- `TWILIO_VERIFY_RESEND_MAX`

### Comportamiento actual

- Canal principal: `sms`
- Proveedor: `Twilio Verify`
- Inicio de envio:
  - `client.verify.v2.services(VERIFY_SID).verifications.create(...)`
- Validacion del codigo:
  - `client.verify.v2.services(VERIFY_SID).verification_checks.create(...)`
- Twilio Verify **no expone el OTP completo** al backend.
- Twilio Verify mantiene el TTL practico de **10 minutos**.
- En este proyecto **no se envia `ttl`** al SDK para evitar incompatibilidades.

### Ejemplo minimo

```python
import os
from twilio.rest import Client

client = Client(
    os.environ["TWILIO_ACCOUNT_SID"],
    os.environ["TWILIO_AUTH_TOKEN"],
)

verification = client.verify.v2.services(
    os.environ["TWILIO_VERIFY_SID"]
).verifications.create(
    to="+573001112233",
    channel=os.getenv("TWILIO_VERIFY_CHANNEL", "sms"),
    template_sid=os.getenv("TWILIO_VERIFY_TEMPLATE_SID") or None,
)

check = client.verify.v2.services(
    os.environ["TWILIO_VERIFY_SID"]
).verification_checks.create(
    to="+573001112233",
    code="123456",
)
```

### Observacion operativa

Si vas a integrar esto en otro proyecto, basta con mover:

- la logica del archivo [twilio_verify.py](C:\.vscode\Preselecta\integrations\services\twilio_verify.py)
- las variables anteriores del `.env`

## 2. Email SMTP

### Archivos fuente actuales

- [otp_service.py](C:\.vscode\Preselecta\integrations\services\otp_service.py)
- [email_otp.html](C:\.vscode\Preselecta\integrations\templates\integrations\email_otp.html)

### Variables requeridas

Tomar desde [.env](C:\.vscode\Preselecta\.env):

- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`
- `DEFAULT_FROM_EMAIL`
- `OTP_AES_KEY_B64`

### Comportamiento actual

- El OTP por EMAIL **si es propio del backend**.
- Se genera internamente un codigo numerico.
- Se guarda:
  - OTP cifrado
  - hash del OTP
  - destino cifrado
  - destino enmascarado
- El correo sale por SMTP usando `EmailMultiAlternatives`.
- El template usado es:
  - [email_otp.html](C:\.vscode\Preselecta\integrations\templates\integrations\email_otp.html)
- El logo intenta adjuntarse inline desde:
  - `static/img/LogoHD.png`
- Si falla el inline image, el template cae al `logo_url` externo.

### Validaciones actuales

- TTL del OTP email: configurado desde servicio OTP.
- Max intentos de verificacion: configurado desde servicio OTP.
- Si el SMTP responde autenticacion invalida:
  - el sistema transforma el error a un mensaje mas claro
  - recomienda revisar `EMAIL_HOST_USER` y `EMAIL_HOST_PASSWORD`

### Ejemplo minimo SMTP

```python
from django.core.mail import EmailMultiAlternatives

msg = EmailMultiAlternatives(
    subject="Codigo OTP Congente",
    body="Codigo OTP de Congente",
    from_email="notificaciones@congente.co",
    to=["destino@correo.com"],
)
msg.attach_alternative("<h1>Tu OTP</h1><p>123456</p>", "text/html")
msg.send(fail_silently=False)
```

## 3. Dependencias relacionadas

Si vas a mover esta integracion al otro proyecto, necesitas al menos:

- `twilio`
- `cryptography`
- `Django` si vas a reutilizar `EmailMultiAlternatives` y render de templates

Si no quieres depender de Django para email, puedes reemplazar el envio por
`smtplib` o por un proveedor transaccional externo, pero debes mantener:

- generacion del OTP
- hash
- cifrado del OTP completo
- cifrado del destino
- auditoria

## 4. Recomendacion

Para integracion limpia en el otro proyecto:

1. Reutilizar Twilio Verify tal cual para SMS.
2. Reutilizar solo la logica de OTP interno para EMAIL.
3. Mantener las credenciales en el `.env` del nuevo proyecto.
4. No copiar secretos a archivos `.md`, `.txt` ni al codigo.

