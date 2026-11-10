"""Procedencia minima del lector institucional; no clasifica por codigo numerico."""
from dataclasses import dataclass
from datetime import datetime
import re

FUENTE = 'LINIX.AP014MCLIENTE.K_NOMINA -> LINIX.AP016MDEFNOMIN.K_NOMINA'


@dataclass(frozen=True, kw_only=True)
class PagaduriaLinix:
    codigo: str | None
    tipo: str | None
    activa: bool | None
    estado: str
    fuente: str
    fechaConsulta: str
    requestId: str

    def __post_init__(self):
        if (self.fuente != FUENTE or self.estado not in ('DISPONIBLE','NO_DISPONIBLE','INCONSISTENTE')
                or self.activa is not None and type(self.activa) is not bool
                or self.codigo is not None and (not isinstance(self.codigo,str) or not re.fullmatch(r'\d{1,10}',self.codigo))
                or self.tipo is not None and (not isinstance(self.tipo,str) or len(self.tipo)!=1)
                or not isinstance(self.requestId,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',self.requestId)):
            raise ValueError('PAGADURIA_PROCEDENCIA_INVALIDA')
        when=datetime.fromisoformat(self.fechaConsulta)
        if when.utcoffset() is None:raise ValueError('PAGADURIA_FECHA_SIN_ZONA')
        if self.estado=='DISPONIBLE' and (not self.codigo or self.tipo is None or self.activa is None):
            raise ValueError('PAGADURIA_INCOMPLETA')


def adaptar_pagaduria(raw, *, request_id, fecha_consulta):
    if raw is None:return None
    keys={'codigo','tipo','activa','estado','fuente','fechaConsulta','requestId'}
    if type(raw) is not dict or set(raw)!=keys:raise ValueError('PAGADURIA_CONTRATO_INVALIDO')
    result=PagaduriaLinix(**raw)
    if result.requestId!=request_id or result.fechaConsulta!=fecha_consulta:
        raise ValueError('PAGADURIA_RESPUESTA_NO_VINCULADA')
    return result
