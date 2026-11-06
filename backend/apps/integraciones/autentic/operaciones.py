"""Journal durable local. No presume idempotencia remota del proveedor."""

from dataclasses import asdict, dataclass, field
import json
import re
import sqlite3

from .contratos import AutenticError, _config_unica


@dataclass(frozen=True)
class EstadoFirma:
    estado: str
    transaccion: str = field(default='', repr=False)


class RegistroFirmas:
    def __init__(self, connection, *, cifrar=None, descifrar=None):
        if not isinstance(connection, sqlite3.Connection) or connection.in_transaction:
            raise AutenticError("JOURNAL_SQLITE_EXCLUSIVO_REQUERIDO")
        self.db = connection
        self._configurar_custodia(cifrar, descifrar)
        self.db.execute("""CREATE TABLE IF NOT EXISTS autentic_operaciones (
            clave TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            estado TEXT NOT NULL, transaccion TEXT NOT NULL DEFAULT '')""")
        self.db.execute("CREATE INDEX IF NOT EXISTS autentic_fingerprint ON autentic_operaciones(fingerprint)")
        self.db.commit()
        if self.custodia_recibos:
            self.db.execute('CREATE TABLE IF NOT EXISTS autentic_recibos (clave TEXT PRIMARY KEY, cifrado TEXT NOT NULL)')
            self.db.commit()

    def _configurar_custodia(self, cifrar, descifrar):
        if (cifrar is not None or descifrar is not None) and not (callable(cifrar) and callable(descifrar)):
            raise AutenticError('CIFRADOR_JOURNAL_REQUERIDO')
        self._cifrar = cifrar
        self._descifrar = descifrar
        self.custodia_recibos = cifrar is not None

    def _contexto_recibo(self):
        return {'tipo': 'JOURNAL_SQLITE'}

    def _sellar_recibo(self, clave, fingerprint, transaccion, recibo):
        from .cliente import ReciboFirma
        try:
            if not self.custodia_recibos or type(recibo) is not ReciboFirma or recibo.transaccion != transaccion:
                raise ValueError
            recibo.validar_recuperacion(recibo.ambiente)
            plain = json.dumps({'version': 'AUTENTIC_JOURNAL_RECIBO_V1', 'clave': clave,
                'fingerprint': fingerprint, 'contexto': self._contexto_recibo(), 'recibo': asdict(recibo)},
                sort_keys=True, separators=(',', ':'))
            encrypted = self._cifrar(plain)
            if (type(encrypted) is not str or not encrypted or encrypted == plain
                    or self._descifrar(encrypted) != plain):
                raise ValueError
            return encrypted
        except Exception:
            raise AutenticError('RECIBO_JOURNAL_NO_CUSTODIADO') from None

    def _abrir_recibo(self, clave, fingerprint, transaccion, cifrado):
        from .cliente import ReciboFirma
        try:
            plain = self._descifrar(cifrado)
            if type(plain) is not str or len(plain) > 100_000:
                raise ValueError
            envelope = json.loads(plain, object_pairs_hook=_config_unica)
            if (set(envelope) != {'version', 'clave', 'fingerprint', 'contexto', 'recibo'}
                    or envelope['version'] != 'AUTENTIC_JOURNAL_RECIBO_V1'
                    or envelope['clave'] != clave or envelope['fingerprint'] != fingerprint
                    or envelope['contexto'] != self._contexto_recibo()):
                raise ValueError
            receipt = ReciboFirma(**envelope['recibo'])
            receipt.validar_recuperacion(receipt.ambiente)
            if receipt.transaccion != transaccion:
                raise ValueError
            return receipt
        except Exception:
            raise AutenticError('RECIBO_JOURNAL_NO_INTEGRO') from None

    def recuperar_recibo(self, clave):
        if not self.custodia_recibos:
            raise AutenticError('CIFRADOR_JOURNAL_REQUERIDO')
        row = self.db.execute('SELECT o.fingerprint, o.estado, o.transaccion, r.cifrado '
            'FROM autentic_operaciones o LEFT JOIN autentic_recibos r ON r.clave=o.clave WHERE o.clave=?',
            (clave,)).fetchone()
        if row is None or row[1] != 'FIRMA_REPORTADA_NO_VERIFICADA':
            raise AutenticError('RECIBO_JOURNAL_NO_DISPONIBLE_NO_REENVIAR')
        return None if row[3] is None else self._abrir_recibo(clave, row[0], row[2], row[3])

    def preparar(self, clave, fingerprint):
        if (type(clave) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', clave)
                or type(fingerprint) is not str or not re.fullmatch(r'[0-9a-f]{64}', fingerprint)):
            raise AutenticError("CLAVE_LOCAL_INVALIDA")
        try:
            # El bloqueo de escritura impide reservar el mismo envio con dos claves.
            self.db.execute("BEGIN IMMEDIATE")
            other = self.db.execute(
                "SELECT clave FROM autentic_operaciones WHERE fingerprint=? AND clave<>? LIMIT 1",
                (fingerprint, clave)).fetchone()
            if other:
                raise AutenticError("ENVIO_DUPLICADO_OTRA_CLAVE")
            row = self.db.execute(
                "SELECT fingerprint, estado, transaccion FROM autentic_operaciones WHERE clave=?",
                (clave,)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO autentic_operaciones VALUES (?, ?, 'PREPARADA', '')", (clave, fingerprint))
                row = (fingerprint, 'PREPARADA', '')
            elif row[0] != fingerprint:
                raise AutenticError("IDEMPOTENCIA_CONFLICTO")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return EstadoFirma(row[1], row[2])

    def iniciar(self, clave):
        changed = self.db.execute("UPDATE autentic_operaciones SET estado='ENVIANDO' WHERE clave=? AND estado='PREPARADA'", (clave,)).rowcount
        self.db.commit()
        if changed != 1:
            raise AutenticError("REENVIO_BLOQUEADO_REQUIERE_CONCILIACION")

    def terminar(self, clave, estado, transaccion="", *, recibo=None):
        if estado not in {"INDETERMINADA", "FIRMA_REPORTADA_NO_VERIFICADA", "CERTIFICACION_RECUPERADA_NO_VERIFICADA"}:
            raise AutenticError("TRANSICION_INVALIDA")
        try:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute("SELECT fingerprint FROM autentic_operaciones WHERE clave=? AND estado='ENVIANDO'",
                                  (clave,)).fetchone()
            if row is None or (recibo is not None and estado != 'FIRMA_REPORTADA_NO_VERIFICADA'):
                raise AutenticError('TRANSICION_INVALIDA')
            encrypted = self._sellar_recibo(clave, row[0], transaccion, recibo) if recibo is not None else None
            self.db.execute("UPDATE autentic_operaciones SET estado=?, transaccion=? WHERE clave=?",
                            (estado, transaccion, clave))
            if encrypted is not None:
                self.db.execute('INSERT INTO autentic_recibos (clave, cifrado) VALUES (?, ?)', (clave, encrypted))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise


class RegistroFirmasExpediente(RegistroFirmas):
    """Same journal protocol, persisted inside the existing dossier PostgreSQL row.

    No SQLite database, new table or provider retry mechanism. The surrounding
    documentary service must first commit the unique document claim.
    """
    def __init__(self,solicitud_id,envio_id,*,alias='default',seccion='journalFirma',cifrar=None,descifrar=None):
        from uuid import UUID
        if alias not in {'default','cotizacion'} or seccion not in {'journalFirma','journalCertificacion'}:
            raise AutenticError('JOURNAL_EXPEDIENTE_INVALIDO')
        self.solicitud_id=UUID(str(solicitud_id));self.envio_id=str(UUID(str(envio_id)))
        self.alias=alias;self.seccion=seccion
        if cifrar is None and descifrar is None:
            from apps.xcore_consumo.services.otp_crypto import encrypt_text, decrypt_text
            cifrar, descifrar = encrypt_text, decrypt_text
        self._configurar_custodia(cifrar, descifrar)

    def _contexto_recibo(self):
        return {'tipo': 'JOURNAL_EXPEDIENTE', 'solicitudId': str(self.solicitud_id),
                'envioId': self.envio_id, 'alias': self.alias, 'seccion': self.seccion}

    def _transicion(self,change):
        from django.db import connections,transaction
        from apps.xcore_consumo.models import SolicitudConsumo
        connection=connections[self.alias]
        if connection.in_atomic_block:raise AutenticError('JOURNAL_REQUIERE_COMMIT_DURABLE')
        if connection.vendor!='postgresql':raise AutenticError('JOURNAL_POSTGRESQL_REQUERIDO')
        with transaction.atomic(using=self.alias,durable=True):
            detail=SolicitudConsumo.objects.using(self.alias).select_for_update().get(solicitud_id=self.solicitud_id)
            data=detail.orchestration_data;history=dict(data['credihoy_autentic_envios_v1'])
            entry=dict(history[self.envio_id])
            if entry['vinculo']['solicitudId']!=str(self.solicitud_id):raise AutenticError('JOURNAL_DE_OTRO_EXPEDIENTE')
            current=entry.get(self.seccion)
            new=change(current)
            if new != current:
                entry[self.seccion]=new;history[self.envio_id]=entry
                detail.orchestration_data={**data,'credihoy_autentic_envios_v1':history}
                detail.save(using=self.alias,update_fields=['orchestration_data','updated_at'])
        return EstadoFirma(new['estado'],new.get('transaccion',''))

    def preparar(self,clave,fingerprint):
        if (type(clave) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',clave)
                or type(fingerprint) is not str or not re.fullmatch('[a-f0-9]{64}',fingerprint)):
            raise AutenticError('CLAVE_LOCAL_INVALIDA')
        def prepare(current):
            if current is not None:
                if current['clave']!=clave or current['fingerprint']!=fingerprint:raise AutenticError('IDEMPOTENCIA_CONFLICTO')
                return current
            return {'clave':clave,'fingerprint':fingerprint,'estado':'PREPARADA','transaccion':''}
        return self._transicion(prepare)

    def iniciar(self,clave):
        def start(current):
            if current is None or current['clave']!=clave or current['estado']!='PREPARADA':
                raise AutenticError('REENVIO_BLOQUEADO_REQUIERE_CONCILIACION')
            return {**current,'estado':'ENVIANDO'}
        return self._transicion(start)

    def terminar(self,clave,estado,transaccion='',*,recibo=None):
        if estado not in {'INDETERMINADA','FIRMA_REPORTADA_NO_VERIFICADA','CERTIFICACION_RECUPERADA_NO_VERIFICADA'}:
            raise AutenticError('TRANSICION_INVALIDA')
        def finish(current):
            if current is None or current['clave']!=clave or current['estado']!='ENVIANDO':raise AutenticError('TRANSICION_INVALIDA')
            updated = {**current,'estado':estado,'transaccion':transaccion}
            if recibo is not None:
                if estado != 'FIRMA_REPORTADA_NO_VERIFICADA':raise AutenticError('TRANSICION_INVALIDA')
                updated['reciboCifrado'] = self._sellar_recibo(clave, current['fingerprint'], transaccion, recibo)
            return updated
        return self._transicion(finish)

    def recuperar_recibo(self, clave):
        saved = []
        def read(current):
            if current is None or current['clave'] != clave or current['estado'] != 'FIRMA_REPORTADA_NO_VERIFICADA':
                raise AutenticError('RECIBO_JOURNAL_NO_DISPONIBLE_NO_REENVIAR')
            saved.append(dict(current))
            return current
        self._transicion(read)
        current = saved[0]
        if 'reciboCifrado' not in current:
            return None
        return self._abrir_recibo(clave, current['fingerprint'], current['transaccion'], current['reciboCifrado'])
