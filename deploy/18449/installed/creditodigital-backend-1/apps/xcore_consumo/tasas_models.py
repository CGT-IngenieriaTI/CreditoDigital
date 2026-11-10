"""Catalogo CrediHoy independiente del legacy de tasas Consumo."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.db import models
from decimal import Decimal


class InmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('HISTORICO_INMUTABLE_CREAR_VERSION')

    def delete(self):
        raise ValidationError('HISTORICO_NO_ELIMINABLE')

    def bulk_create(self, *args, **kwargs):
        raise ValidationError('USAR_SERVICIO_AUDITADO')


class RegistroInmutable(models.Model):
    objects = InmutableQuerySet.as_manager()
    creado_at = models.DateTimeField(auto_now_add=True)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('HISTORICO_INMUTABLE_CREAR_VERSION')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('HISTORICO_NO_ELIMINABLE')


class PerfilTasasCredihoy(models.Model):
    producto = models.CharField(max_length=3, default='390')
    modalidad = models.CharField(max_length=3, default='111')

    class Meta:
        constraints = [models.UniqueConstraint(fields=('producto','modalidad'), name='ch_tasas_perfil_unico')]
        permissions = [('administrar_tasas_credihoy','Crear versiones de tasas CrediHoy'),
            ('aprobar_tasas_credihoy','Ratificar soporte y condiciones CrediHoy'),
            ('activar_tasas_credihoy','Activar y cerrar condiciones CrediHoy')]


class CondicionComercialBase(RegistroInmutable):
    perfil = models.ForeignKey(PerfilTasasCredihoy, on_delete=models.PROTECT)
    numero_version = models.PositiveIntegerField()
    linea = models.CharField(max_length=12, choices=(('VENTANILLA','Ventanilla'),('LIBRANZA','Libranza')))
    desde = models.DateField(null=True, blank=True)
    hasta = models.DateField(null=True, blank=True)
    documento = models.CharField(max_length=80)
    version_documento = models.CharField(max_length=40)
    referencia = models.CharField(max_length=240)
    soporte_sha256 = models.CharField(max_length=64, validators=[RegexValidator(r'^[a-f0-9]{64}$')])
    observacion = models.CharField(max_length=500, blank=True)

    class Meta:
        abstract = True

    def clean(self):
        if self.desde and self.hasta and self.hasta <= self.desde:
            raise ValidationError('VIGENCIA_FIN_DEBE_SER_POSTERIOR_INICIO')
        if self.perfil_id and (self.perfil.producto,self.perfil.modalidad) != ('390','111'):
            raise ValidationError('PERFIL_CREDIHOY_INCOMPATIBLE')


def porcentaje():
    return models.DecimalField(max_digits=10, decimal_places=6,
        validators=[MinValueValidator(Decimal('0.000001')),MaxValueValidator(Decimal('100'))])


class TasaComercialCredihoy(CondicionComercialBase):
    tasa_ea = porcentaje()
    tasa_namv = porcentaje()
    tasa_mv_publicada = porcentaje()

    class Meta:
        constraints = [models.UniqueConstraint(fields=('perfil','linea','numero_version'),name='ch_tasa_version_unica')]
        ordering = ('linea','-numero_version')


class AsignacionTarifaCredihoy(CondicionComercialBase):
    alcance = models.CharField(max_length=5, default='MEDIO', choices=(('MEDIO','Medio individual'),('TIPO','Tipo de pagaduria')))
    medio_pago = models.CharField(max_length=3, blank=True)
    tipo_nomina = models.CharField(max_length=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=('perfil','alcance','medio_pago','tipo_nomina','numero_version'),name='ch_asignacion_version_unica')]
        ordering = ('medio_pago','-numero_version')

    def clean(self):
        super().clean()
        if self.alcance == 'TIPO':
            if self.medio_pago or self.tipo_nomina not in ('N','V'):
                raise ValidationError('TIPO_PAGADURIA_INVALIDO')
            if self.tipo_nomina == 'V' and self.linea != 'VENTANILLA':
                raise ValidationError('VENTANILLA_REQUIERE_LINEA_VENTANILLA')
        elif self.alcance != 'MEDIO' or (self.medio_pago,self.tipo_nomina) not in (('100','N'),('999','V')):
            raise ValidationError('MEDIO_TECNICO_NO_HOMOLOGADO')


class SeleccionTarifaCredihoy(RegistroInmutable):
    """Evidencia de seleccion, no modifica condiciones ni evaluacion original."""
    solicitud = models.ForeignKey('solicitudes.Solicitud', on_delete=models.PROTECT)
    request_id = models.UUIDField(unique=True)
    evidencia = models.JSONField(blank=True)
    evidencia_sha256 = models.CharField(max_length=64)
    resultado = models.JSONField()


class EventoTasasCredihoy(RegistroInmutable):
    tasa = models.ForeignKey(TasaComercialCredihoy, on_delete=models.PROTECT, null=True, blank=True, related_name='eventos')
    asignacion = models.ForeignKey(AsignacionTarifaCredihoy, on_delete=models.PROTECT, null=True, blank=True, related_name='eventos')
    accion = models.CharField(max_length=12,choices=tuple((x,x) for x in ('CREADA','APROBADA','ACTIVADA','CERRADA')))
    referencia_autorizacion = models.CharField(max_length=240)
    verificacion = models.JSONField(default=dict, blank=True)
    cierre_desde = models.DateField(null=True, blank=True)
    idempotency_key = models.UUIDField(unique=True)
    request_sha256 = models.CharField(max_length=64)

    class Meta:
        ordering = ('creado_at','pk')
        constraints = [models.CheckConstraint(check=(models.Q(tasa__isnull=False,asignacion__isnull=True)
            | models.Q(tasa__isnull=True,asignacion__isnull=False)),name='ch_evento_un_objetivo')]
