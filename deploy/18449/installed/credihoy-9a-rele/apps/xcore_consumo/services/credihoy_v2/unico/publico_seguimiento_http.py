"""Minimal read contract to mount only in the authorized data-owning service."""
from django.db import DatabaseError
from . import publico_seguimiento as owner
from . import publico_institucional as wire
from .publico_institucional_http import InstitucionalView


class SolicitudesTitularView(InstitucionalView):
    def authorize_request(self,request):
        self.rid,self.identity=owner.authorize(request)

    def post(self,request):
        try:
            if not owner.claim_delegation(self.rid):raise ValueError('DELEGACION_YA_UTILIZADA')
            rows=owner.read_owned(self.identity,request.data)
        except DatabaseError:raise wire.NoDisponible from None
        owner.audit_read(self.rid,len(rows))
        return self.packet(sesionReferencia=request.data['accesoId'],solicitudes=rows)
