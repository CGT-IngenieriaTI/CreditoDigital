import calendar
from datetime import date


class MicrocreditoPlanDateError(ValueError):
    pass


def calculate_initial_payment_date(day_of_month, today=None):
    """
    Calcula la primera fecha de pago para microcredito.

    Regla de negocio: se intenta programar el dia solicitado en el mes siguiente.
    Si esa fecha queda a menos de 30 dias desde hoy, se corre al mes subsiguiente.
    """
    day = _normalize_day(day_of_month)
    base_date = today or date.today()
    candidate = _date_in_month_offset(base_date, day, 1)
    if (candidate - base_date).days < 30:
        candidate = _date_in_month_offset(base_date, day, 2)
    return candidate


def format_linix_date(value):
    return value.strftime("%m/%d/%Y")


def _normalize_day(day_of_month):
    try:
        day = int(day_of_month)
    except (TypeError, ValueError) as exc:
        raise MicrocreditoPlanDateError("fechaInicialPlanPagos debe ser un dia numerico") from exc

    if day < 1 or day > 31:
        raise MicrocreditoPlanDateError("fechaInicialPlanPagos debe estar entre 1 y 31")
    return day


def _date_in_month_offset(base_date, day, month_offset):
    month_index = base_date.month - 1 + month_offset
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))
