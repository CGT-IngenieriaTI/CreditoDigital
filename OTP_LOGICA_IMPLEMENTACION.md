# Logica OTP SMS y EMAIL

## Objetivo
Documentar unicamente la logica OTP que debe replicarse en otro proyecto:
- envio por SMS con Twilio Verify
- envio por EMAIL con OTP interno
- persistencia minima en base de datos
- firma/autorizacion del PDF con canal OTP

## 1. Logica SMS

### Proveedor
- SMS usa `Twilio Verify`.
- El backend no genera el OTP SMS.
- El backend no conoce el OTP completo.
- El backend solicita a Twilio que envie el codigo al numero destino.

### Conexion con Verify
Credenciales necesarias:
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_VERIFY_SID`
- `TWILIO_VERIFY_TEMPLATE_SID` opcional

Cliente usado:
- `integrations/services/twilio_verify.py`

Secuencia:
1. Instanciar `TwilioVerifyClient()`.
2. Ejecutar `start_verification(to_number, channel="sms", template_sid=...)`.
3. Twilio responde con un `verification.sid`.
4. Ese `sid` se guarda para trazabilidad.
5. Cuando el usuario ingresa el codigo, se ejecuta `check_verification(to_number, code)`.
6. Si Twilio responde `approved`, el OTP SMS se considera valido.

### Regla clave
- En SMS no debe guardarse el OTP completo en la base de datos.
- Solo se guarda el rastro tecnico de la verificacion.

## 2. Logica EMAIL

### Proveedor
- EMAIL usa OTP interno generado por backend.
- El envio se hace por SMTP.
- El backend si controla completamente el OTP.

### Credenciales y configuracion
Variables necesarias:
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`
- `DEFAULT_FROM_EMAIL`
- `OTP_AES_KEY_B64`
- `OTP_EMAIL_CONSENT_URL`
- `OTP_EMAIL_LOGO_URL` opcional

Archivos clave:
- `integrations/services/otp_service.py`
- `integrations/services/otp_crypto.py`
- `integrations/templates/integrations/email_otp.html`

### Generacion
1. Generar OTP numerico usando `secrets.choice(string.digits)`.
2. Longitud actual: 6 digitos.
3. Vigencia actual por defecto: 10 minutos.

### Persistencia segura
Para EMAIL el backend guarda dos cosas del OTP:
- `otp_code_encrypted`: OTP completo cifrado con AES-256 GCM
- `otp_hash`: hash del OTP para validacion segura

Regla:
- La validacion real se hace contra `otp_hash`.
- El OTP cifrado existe solo para trazabilidad controlada y para imprimirlo en el PDF final cuando el canal fue EMAIL.

### Envio
1. Renderizar `email_otp.html` con:
   - nombre asociado
   - otp_code
   - vigencia_minutos
   - consentimiento_url
   - logo
2. Enviar con `EmailMultiAlternatives`.
3. Si falla SMTP, marcar error de envio.

### Validacion
1. El usuario ingresa el OTP.
2. El backend compara con `check_password(otp_code, otp_hash)`.
3. Si coincide, el OTP EMAIL se considera valido.
4. Si no coincide, se incrementan intentos y se rechaza.

## 3. Cifrado OTP y destino

### OTP
Archivo:
- `integrations/services/otp_crypto.py`

Logica:
- Se usa `AESGCM`.
- La llave llega en `OTP_AES_KEY_B64`.
- Debe decodificar a 32 bytes.

### Destino
Tambien se cifra el destino completo cuando aplica:
- numero completo
- correo completo

Regla:
- En UI y auditoria se muestra enmascarado.
- El valor completo queda cifrado para trazabilidad interna.

## 4. Que guardar en BD

## SMS
Guardar minimo:
- `channel = sms`
- `provider = twilio_verify`
- `destination_masked`
- `destination_full_encrypted`
- `verification_sid`
- `verification_check_sid`
- `status`
- `generated_at`
- `expires_at`
- `verified_at`
- `attempts_used`
- `validation_result`
- `ip_address`
- `user_agent`

No guardar:
- OTP completo
- hash del OTP SMS

## EMAIL
Guardar minimo:
- `channel = email`
- `provider = internal_email`
- `destination_masked`
- `destination_full_encrypted`
- `otp_code_encrypted`
- `otp_hash`
- `otp_masked`
- `status`
- `generated_at`
- `expires_at`
- `verified_at`
- `attempts_used`
- `validation_result`
- `ip_address`
- `user_agent`

## 5. Como queda el PDF firmado con OTP

Archivo clave:
- `integrations/services/consent_pdf.py`

La firma aqui no es firma digital criptografica. Lo que se hace es:
- generar el PDF de consentimiento
- insertar en el footer la evidencia del canal OTP usado
- guardar ese PDF como soporte del consentimiento aprobado

### Regla para SMS
Como el backend no conoce el OTP real de Twilio Verify, el footer debe indicar solo el canal y destino.

Texto actual:
- `Documento autorizado mediante OTP vía SMS al número {numero}`

### Regla para EMAIL
Como el backend si conoce el OTP validado, el footer puede incluir el codigo.

Texto actual:
- `Documento autorizado mediante OTP {otp} vía EMAIL al correo {correo}`

## 6. Recomendacion sobre hash o ID en PDF

Si en el otro proyecto no quieres imprimir el OTP de EMAIL en el PDF, la alternativa correcta es poner un identificador de trazabilidad, no el hash tecnico.

Recomendacion:
- usar `transaction_uuid` o `public_id`
- no imprimir `otp_hash`
- no imprimir ciphertext

Motivo:
- el hash no sirve visualmente para negocio
- el UUID si sirve para cruce probatorio entre PDF y BD

Ejemplo recomendado si quieren endurecer trazabilidad:
- `Documento autorizado mediante OTP vía EMAIL al correo {correo}. Id transaccion: {transaction_uuid}`
- `Documento autorizado mediante OTP vía SMS al número {numero}. Id transaccion: {transaction_uuid}`

## 7. Regla exacta para implementar igual

### SMS
- enviar OTP con Twilio Verify
- guardar `verification_sid`
- validar con Verify
- si Twilio responde `approved`, aprobar consentimiento
- en PDF dejar canal SMS y numero
- no guardar OTP SMS completo

### EMAIL
- generar OTP interno
- cifrar OTP completo
- guardar hash
- enviar por SMTP
- validar con hash
- si aprueba, generar PDF con OTP y correo

## 8. Archivos exactos a revisar
- [integrations/services/twilio_verify.py](C:\.vscode\Preselecta\integrations\services\twilio_verify.py)
- [integrations/services/otp_service.py](C:\.vscode\Preselecta\integrations\services\otp_service.py)
- [integrations/services/otp_crypto.py](C:\.vscode\Preselecta\integrations\services\otp_crypto.py)
- [integrations/services/consent_pdf.py](C:\.vscode\Preselecta\integrations\services\consent_pdf.py)
- [integrations/templates/integrations/email_otp.html](C:\.vscode\Preselecta\integrations\templates\integrations\email_otp.html)
- [integrations/services/twilio_verify.py](C:\.vscode\Preselecta\integrations\services\twilio_verify.py)
- [preselecta_web/settings.py](C:\.vscode\Preselecta\preselecta_web\settings.py)

## 9. Decision practica para el otro proyecto
Si quieres replicar exactamente este comportamiento:
- SMS: Twilio Verify, sin OTP almacenado localmente
- EMAIL: OTP propio, cifrado + hash
- PDF: texto de autorizacion por canal

Si quieres endurecerlo un poco mejor:
- mantener igual SMS
- mantener igual EMAIL
- reemplazar en el PDF el OTP visible por `transaction_uuid`
- dejar el OTP completo solo en BD cifrada
