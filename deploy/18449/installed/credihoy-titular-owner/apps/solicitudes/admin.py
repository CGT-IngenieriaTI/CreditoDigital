from django.contrib import admin
from django.db.models import BooleanField, Case, CharField, Exists, F, OuterRef, Q, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Replace
from django.utils.html import format_html, format_html_join

from .models import Solicitud


def _perfil_credihoy(queryset):
    from apps.xcore_consumo.models import EvaluacionConsumo
    from apps.xcore_consumo.services.credihoy_v2.unico.funcional import KEY, VERSION
    evidence = EvaluacionConsumo.objects.filter(solicitud_id=OuterRef('pk'), **{
        f'resultados__{KEY}__versionFuncional': VERSION,
        f'resultados__{KEY}__preparacionRadicacion__producto__codigo': '390',
        f'resultados__{KEY}__preparacionRadicacion__producto__modalidad': '111',
        f'resultados__{KEY}__preparacionRadicacion__producto__destino': 'CFA',
    }).annotate(
        # SQLite stores UUIDs without separators; PostgreSQL uses native UUIDs.
        _evidence_case=Replace(KeyTextTransform('solicitudId', KeyTextTransform(KEY, 'resultados')),
            Value('-'), Value(''), output_field=CharField()),
        _linked_case=Replace(Cast('solicitud_id', CharField()), Value('-'), Value('')),
    ).filter(_evidence_case=F('_linked_case'))
    return queryset.annotate(_perfil_credihoy_acreditado=Exists(evidence))


class PerfilCredihoyFilter(admin.SimpleListFilter):
    title = 'Perfil CrediHoy'
    parameter_name = 'credihoy'

    def lookups(self, request, model_admin):
        return (('acreditado', '390/111/CFA: contrato digital persistido'),)

    def queryset(self, request, queryset):
        if self.value() == 'acreditado':
            from apps.xcore_consumo.services.credihoy_v2.unico.consulta_controlada import PRODUCTO
            return queryset.filter(producto=PRODUCTO, _perfil_credihoy_acreditado=True)
        return queryset


class IncidenciaCredihoyFilter(admin.SimpleListFilter):
    title = 'Incidencia registrada'
    parameter_name = 'incidencia_credihoy'

    def lookups(self, request, model_admin):
        return (('si', 'Con registro'), ('no', 'Sin registro'))

    def queryset(self, request, queryset):
        from apps.xcore_consumo.services.credihoy_v2.unico.linix_estado import STATE, KEY
        condition = (~Q(ultimo_error='') | Q(**{
            f'consumo_detail__orchestration_data__{KEY}__estado': STATE}) | Q(**{
            f'consumo_detail__orchestration_data__{KEY}__incidencias__registradas__gt':0}))
        if self.value() in ('si','no'):
            # Missing JSON keys are SQL NULL, not a recorded incident.
            return queryset.annotate(_incidencia_registrada=Case(When(condition,then=Value(True)),
                default=Value(False),output_field=BooleanField())).filter(
                    _incidencia_registrada=self.value()=='si')
        return queryset


@admin.register(Solicitud)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ("numero_solicitud", "solicitante", "estado", "paso_actual", "created_at")
    list_filter = (PerfilCredihoyFilter, IncidenciaCredihoyFilter, "estado", "paso_actual", "created_at")
    list_per_page = 20
    list_max_show_all = 0
    search_fields = (
        "numero_solicitud",
        "solicitante__numero_identificacion",
        "solicitante__primer_apellido",
    )
    readonly_fields = ("numero_solicitud", "created_at", "updated_at")

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('consumo_detail', 'solicitante')
        if not request.user.has_perm('solicitudes.view_solicitud'):
            return queryset.none()
        if not request.user.is_superuser:
            queryset = queryset.filter(asesor=request.user)
        return _perfil_credihoy(queryset)

    def has_view_permission(self, request, obj=None):
        return (request.user.has_perm('solicitudes.view_solicitud')
            and (obj is None or request.user.is_superuser or obj.asesor_id == request.user.pk))

    def has_change_permission(self, request, obj=None):
        from apps.xcore_consumo.services.credihoy_v2.unico.consulta_controlada import PRODUCTO
        if request.GET.get('credihoy') == 'acreditado' or (obj and obj.producto == PRODUCTO):
            return False
        return self.has_view_permission(request, obj) and super().has_change_permission(request, obj)

    def has_add_permission(self, request):
        return request.GET.get('credihoy') != 'acreditado' and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        from apps.xcore_consumo.services.credihoy_v2.unico.consulta_controlada import PRODUCTO
        if request.GET.get('credihoy') == 'acreditado' or (obj and obj.producto == PRODUCTO):
            return False
        return self.has_view_permission(request, obj) and super().has_delete_permission(request, obj)

    def get_actions(self, request):
        # Bulk deletion cannot bypass the per-object read-only credit boundary.
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return {} if request.GET.get('credihoy') == 'acreditado' else actions

    def get_list_display(self, request):
        if request.GET.get('credihoy') == 'acreditado':
            return ('numero_solicitud', 'perfil_credihoy', 'estado_operativo', 'estudio_credihoy',
                'radicacion_credihoy', 'workflow_credihoy', 'decision_credihoy',
                'formalizacion_credihoy', 'incidencia_credihoy', 'created_at')
        return super().get_list_display(request)

    def get_search_fields(self, request):
        if request.GET.get('credihoy') == 'acreditado':
            return ('numero_solicitud',
                'consumo_detail__orchestration_data__credihoy_linix_administrativo__kFlujo')
        return super().get_search_fields(request)

    def _lectura_credihoy(self, obj):
        if hasattr(obj, '_lectura_admin_credihoy'):
            return obj._lectura_admin_credihoy
        from apps.xcore_consumo.services.credihoy_v2.unico.estado_credito import construir_estado
        from apps.xcore_consumo.services.credihoy_v2.unico.funcional import leer_estado_funcional, VERSION
        from apps.xcore_consumo.services.credihoy_v2.unico.linix_estado import presentar
        from django.db import DatabaseError
        result = None
        try:
            packet = leer_estado_funcional(obj)
            stored = packet['resultado']
            profile = stored['preparacionRadicacion']['producto']
            if (stored['solicitudId'] != str(obj.pk) or stored['versionFuncional'] != VERSION
                    or tuple(profile[k] for k in ('codigo', 'modalidad', 'destino')) != ('390', '111', 'CFA')):
                raise ValueError('PERFIL_DIGITAL_NO_ACREDITADO')
            packet['estadoLinix'] = presentar(obj)
            result = construir_estado(obj, packet)['operador']
        except (ValueError, KeyError, TypeError, AttributeError, DatabaseError):
            pass
        obj._lectura_admin_credihoy = result
        return result

    @admin.display(description='Perfil digital (no SC)')
    def perfil_credihoy(self, obj):
        return '390/111/CFA acreditado' if self._lectura_credihoy(obj) else 'Evidencia no disponible o inconsistente'

    @admin.display(description='Estado operativo (no decision)', ordering='estado')
    def estado_operativo(self, obj):
        return obj.get_estado_display()

    def _etapa(self, obj, key):
        state = self._lectura_credihoy(obj)
        return state['etapasProceso'][key]['estado'] if state else 'NO_DISPONIBLE'

    @admin.display(description='Preparacion para estudio')
    def estudio_credihoy(self, obj):
        state = self._lectura_credihoy(obj)
        if state:
            phases = state['etapasProceso']
            return phases['evaluacionPreliminar']['estado'] + ' / ' + phases['requisitosRadicacionEstudio']['estado']
        return 'NO_DISPONIBLE'

    @admin.display(description='Radicacion / referencia')
    def radicacion_credihoy(self, obj):
        state = self._lectura_credihoy(obj)
        if not state:
            return 'NO_DISPONIBLE'
        phase = state['etapasProceso']['radicacion']
        institution=state['etapasProceso']['workflowInstitucional'].get('lecturaInstitucional')
        if institution:
            return format_html('{}; flujo: {}; SC: {}',phase['estado'],institution['kFlujo'],institution['numeroSc'])
        return format_html('{}; flujo: {}; SC no acreditada en esta lectura',
            phase['estado'], phase['flujoReferenciado'] or 'No disponible')

    @admin.display(description='Workflow / observacion')
    def workflow_credihoy(self, obj):
        state = self._lectura_credihoy(obj)
        if not state:
            return 'NO_DISPONIBLE'
        workflow=state['etapasProceso']['workflowInstitucional']
        if workflow.get('lecturaInstitucional'):
            data=workflow['lecturaInstitucional'];task=data['actividad']
            return format_html('{}; actividad {}; tarea {}; corte {}',workflow['estado'],task['codigo'],
                               task['kSerie'],data['actualizadoAt'])
        observed = workflow['observacionLocal']
        if observed:
            return format_html('{}; actividad observada {}; corte {}; tarea activa no acreditada',
                observed['estado'], observed['actividadObservada']['codigo'], observed['actualizadoAt'])
        return 'Sin evidencia conciliada de tarea activa'

    @admin.display(description='Decision humana')
    def decision_credihoy(self, obj):
        return self._etapa(obj, 'decisionHumana')

    @admin.display(description='Formalizacion')
    def formalizacion_credihoy(self, obj):
        return self._etapa(obj, 'formalizacion')

    @admin.display(description='Incidencia')
    def incidencia_credihoy(self, obj):
        state = self._lectura_credihoy(obj)
        if state and state['etapasProceso']['workflowInstitucional'].get('lecturaInstitucional'):
            counts=state['etapasProceso']['workflowInstitucional']['lecturaInstitucional']['incidencias']
            if counts['registradas'] is not None:
                return format_html('Incidencias registradas: {}; activas: {}; ejecutadas: {}',
                    counts['registradas'],counts['enTareasActivas'],counts['enTareasEjecutadas'])
            return 'Incidencias no disponibles al corte'
        if state and state['etapasProceso']['workflowInstitucional']['observacionLocal']:
            return 'Incidencia local registrada; requiere conciliacion'
        return 'Error operativo registrado (detalle reservado)' if obj.ultimo_error else 'Sin registro disponible'

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        from apps.xcore_consumo.services.credihoy_v2.unico.consulta_controlada import PRODUCTO
        if (obj and obj.producto == PRODUCTO and request.user.has_perm('solicitudes.view_solicitud')
                and (request.user.is_superuser or obj.asesor_id == request.user.pk)):
            if request.user.has_perm('documentos.view_eventodocumental'):
                obj._actor_historial_documental = request.user
                return (*fields, 'preparacion_credihoy', 'historial_documental')
            return (*fields, 'preparacion_credihoy')
        return fields

    @admin.display(description='Historial documental (sin firma ni identidad acreditadas)')
    def historial_documental(self, obj):
        from apps.documentos.services import leer_historial_documental
        actor = getattr(obj, '_actor_historial_documental', None)
        if actor is None:
            return 'Lectura documental no autorizada.'
        history = leer_historial_documental(obj.pk, actor=actor)
        rows = format_html_join('', '<li>{} / {} / version {} / {} / {}</li>',
            ((r['fecha'], r['tipo'], r['version'], r['documento'], r['integridad'])
                for r in history['eventos']))
        return format_html('<p>Eventos: {}. Aceptaciones historicas conservadas: {}. '
            'Las respuestas administrativas no acreditan aceptacion del titular ni firma.</p><ul>{}</ul>',
            len(history['eventos']), len(history['historico']['aceptaciones']), rows)

    @admin.display(description='Preparacion CrediHoy (solo lectura)')
    def preparacion_credihoy(self, obj):
        state = self._lectura_credihoy(obj)
        if state is None:
            return 'Evidencia no disponible o inconsistente. No habilita acciones crediticias.'
        formal = state['preparacionFormalizacion']
        etapas = state['etapasProceso']
        phase_rows = format_html_join('', '<li>{}: {}</li>', (
            ('Evaluacion preliminar', etapas['evaluacionPreliminar']['estado']),
            ('Requisitos para radicar a estudio', etapas['requisitosRadicacionEstudio']['estado']),
            ('Radicacion', etapas['radicacion']['estado']),
            ('Workflow institucional', etapas['workflowInstitucional']['estado']),
            ('Decision humana LINIX', etapas['decisionHumana']['estado']),
            ('Formalizacion', etapas['formalizacion']['estado']),
        ))
        observation = etapas['workflowInstitucional']['observacionLocal']
        institution=etapas['workflowInstitucional'].get('lecturaInstitucional')
        observed = (format_html('<p>LINIX al corte {}: SC {}, aprobada por {} el {}; pagare {}; '
                    'tarea pendiente {}. Firma y liquidacion no ejecutadas.</p>',institution['actualizadoAt'],
                    institution['numeroSc'],institution['aprobacion']['aprobadorReferencia'],
                    institution['aprobacion']['fecha'],institution['pagare']['numero'],institution['actividad']['kSerie'])
            if institution else format_html('<p>Observacion local: {}. Actividad {}. Corte: {}. '
            'No acredita tarea activa ni aprobacion.</p>', observation['estado'],
            observation['actividadObservada']['codigo'], observation['actualizadoAt'])
            if observation else 'Sin observacion de workflow conciliada disponible.')
        rows = format_html_join('', '<li>{}: {}. Responsable: {}</li>',
            ((v['codigo'], v['estado'], v['responsable']) for v in formal['requisitos']))
        return format_html('<h3>Etapas del proceso (solo lectura)</h3><ul>{}</ul>'
            '<div>{}</div>'
            '<p>SC y tarea activa requieren conciliacion; un grant tecnico no acredita '
            'la decision humana. Orden documental pendiente de confirmacion institucional.</p>'
            '<p>Revision de decision: {}. Radicacion: {}.</p><ul>{}</ul>'
            '<p>Aceptaciones documentales registradas: {}. La autorizacion de centrales '
            'no acredita aceptacion de oferta ni firma del contrato. Sin acciones habilitadas.</p>',
            phase_rows, observed, state['preparacionDecision']['estado'], formal['accionRadicacion'], rows,
            len(formal['documentos']['aceptaciones']))

# Register your models here.
