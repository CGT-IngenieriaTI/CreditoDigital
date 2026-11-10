"""Dedicated Verify service provisioning. No contact, message or OTP operations."""
import re

NAME = 'Congente Acceso CrediHoy 9A'


def configurar(client, *, sid_firma, sid_existente=None, permitir_creacion=False):
    if not re.fullmatch(r'VA[a-fA-F0-9]{32}', sid_firma or ''):
        raise ValueError('SERVICIO_FIRMA_NO_IDENTIFICADO')
    if sid_existente:
        if not re.fullmatch(r'VA[a-fA-F0-9]{32}', sid_existente) or sid_existente == sid_firma:
            raise ValueError('SERVICIO_ACCESO_NO_SEPARADO')
        resource = client.verify.v2.services(sid_existente).fetch()
    else:
        matches = []
        # Bound inspection; reaching the cap is ambiguous, never create blindly.
        rows = client.verify.v2.services.list(limit=101, page_size=50)
        if len(rows) > 100:
            raise ValueError('CATALOGO_VERIFY_REQUIERE_CONCILIACION')
        matches = [row for row in rows if row.friendly_name == NAME]
        if len(matches) > 1:
            raise ValueError('SERVICIO_VERIFY_DUPLICADO')
        if matches:
            resource = client.verify.v2.services(matches[0].sid).fetch()
        elif permitir_creacion:
            resource = client.verify.v2.services.create(friendly_name=NAME, code_length=6,
                do_not_share_warning_enabled=True, custom_code_enabled=False,
                lookup_enabled=False, psd2_enabled=False)
        else:
            raise ValueError('CREACION_VERIFY_INCIERTA_NO_REPETIR')
    if (not re.fullmatch(r'VA[a-fA-F0-9]{32}', resource.sid or '') or resource.sid == sid_firma
            or resource.friendly_name != NAME or resource.code_length != 6
            or resource.do_not_share_warning_enabled is not True
            or resource.custom_code_enabled is not False or resource.psd2_enabled is not False):
        raise ValueError('CONFIGURACION_VERIFY_NO_ACREDITADA')
    return {'sid': resource.sid, 'canalesPreparados': ['SMS'], 'codigoLongitud': 6,
        'separadoConsentimiento': True, 'OTPEnviados': 0}
