"""Journal durable local. No presume idempotencia remota del proveedor."""

from dataclasses import dataclass
import re
import sqlite3

from .contratos import AutenticError


@dataclass(frozen=True)
class EstadoFirma:
    estado: str
    transaccion: str = ""


class RegistroFirmas:
    def __init__(self, connection):
        if not isinstance(connection, sqlite3.Connection) or connection.in_transaction:
            raise AutenticError("JOURNAL_SQLITE_EXCLUSIVO_REQUERIDO")
        self.db = connection
        self.db.execute("""CREATE TABLE IF NOT EXISTS autentic_operaciones (
            clave TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            estado TEXT NOT NULL, transaccion TEXT NOT NULL DEFAULT '')""")
        self.db.execute("CREATE INDEX IF NOT EXISTS autentic_fingerprint ON autentic_operaciones(fingerprint)")
        self.db.commit()

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

    def terminar(self, clave, estado, transaccion=""):
        if estado not in {"INDETERMINADA", "FIRMA_REPORTADA_NO_VERIFICADA", "CERTIFICACION_RECUPERADA_NO_VERIFICADA"}:
            raise AutenticError("TRANSICION_INVALIDA")
        changed = self.db.execute("UPDATE autentic_operaciones SET estado=?, transaccion=? WHERE clave=? AND estado='ENVIANDO'",
                                  (estado, transaccion, clave)).rowcount
        self.db.commit()
        if changed != 1:
            raise AutenticError("TRANSICION_INVALIDA")


class RegistroFirmasExpediente(RegistroFirmas):
    """Same journal protocol, persisted inside the existing dossier PostgreSQL row.

    No SQLite database, new table or provider retry mechanism. The surrounding
    documentary service must first commit the unique document claim.
    """
    def __init__(self,solicitud_id,envio_id,*,alias='default',seccion='journalFirma'):
        from uuid import UUID
        if alias not in {'default','cotizacion'} or seccion not in {'journalFirma','journalCertificacion'}:
            raise AutenticError('JOURNAL_EXPEDIENTE_INVALIDO')
        self.solicitud_id=UUID(str(solicitud_id));self.envio_id=str(UUID(str(envio_id)))
        self.alias=alias;self.seccion=seccion

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

    def terminar(self,clave,estado,transaccion=''):
        if estado not in {'INDETERMINADA','FIRMA_REPORTADA_NO_VERIFICADA','CERTIFICACION_RECUPERADA_NO_VERIFICADA'}:
            raise AutenticError('TRANSICION_INVALIDA')
        def finish(current):
            if current is None or current['clave']!=clave or current['estado']!='ENVIANDO':raise AutenticError('TRANSICION_INVALIDA')
            return {**current,'estado':estado,'transaccion':transaccion}
        return self._transicion(finish)
