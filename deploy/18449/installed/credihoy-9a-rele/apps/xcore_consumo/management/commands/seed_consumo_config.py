import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.xcore_consumo.models import (
    ConfiguracionAgenciaCanal,
    ConfiguracionGastosFamiliares,
    ConfiguracionRegresion,
    TasaInteresConsumo,
)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="latin-1"))


def _decode_mojibake(text: str) -> str:
    if not text:
        return text
    if any(token in text for token in ("Ã", "Â", "â", "�")):
        for source, target in (("latin-1", "utf-8"), ("cp1252", "utf-8")):
            try:
                return text.encode(source).decode(target)
            except Exception:
                continue
    return text


def _fix_text(value):
    if not isinstance(value, str):
        return value
    text = _decode_mojibake(value.strip())
    return text.strip()


def _table_rows(payload, table_name: str):
    if isinstance(payload, dict):
        return payload.get(table_name, payload.get(table_name.lower(), []))
    if isinstance(payload, list):
        if not payload:
            return []
        typed_items = [item for item in payload if isinstance(item, dict) and "type" in item]
        if typed_items and all(item.get("type") == "table" for item in typed_items):
            for item in payload:
                if isinstance(item, dict) and item.get("type") == "table" and item.get("name") == table_name:
                    return item.get("data", [])
            return []
        if all(isinstance(item, dict) for item in payload):
            return payload
    return []


def _as_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def _as_float(value, default=0.0):
    try:
        return float(str(value).strip().replace(",", "."))
    except Exception:
        return default


def _normalize_rate_text(value: str) -> str:
    text = _fix_text(value)
    replacements = {
        "inversión": "inversion",
        "Educación": "Educacion",
        "Cesión": "Cesion",
        "Nómina": "Nomina",
        "Débito": "Debito",
        "automático": "automatico",
        "Línea": "Linea",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _default_json_path(project_root: Path, base_name: str) -> Path:
    stem = Path(base_name).stem
    clean_candidate = project_root / f"{stem}_clean.json"
    if clean_candidate.exists():
        return clean_candidate
    return project_root / base_name


def _build_unified_payload(project_root: Path, *, agencias_file=None, gastos_file=None, regresion_file=None, tasas_file=None):
    agencias_payload = _load_json(Path(agencias_file) if agencias_file else _default_json_path(project_root, "agencias.json"))
    gastos_payload = _load_json(Path(gastos_file) if gastos_file else _default_json_path(project_root, "conf_gastosfamiliares.json"))
    regresion_payload = _load_json(Path(regresion_file) if regresion_file else _default_json_path(project_root, "configuraciones_configuracion.json"))
    tasas_payload = _load_json(Path(tasas_file) if tasas_file else _default_json_path(project_root, "configuraciones_tasainteres.json"))

    agencias = [
        {
            "canal": _fix_text(row.get("canal", "")),
            "codigo": _fix_text(row.get("codigo", "")),
            "puntos": _as_int(row.get("puntos", 0)),
        }
        for row in _table_rows(agencias_payload, "agencias")
    ]
    gastos_familiares = [
        {
            "salario_minimo": _as_int(row.get("salario_minimo", 0)),
            "cant_personasacargo": _as_int(row.get("cant_personasacargo", 0)),
            "porcentaje": _as_float(row.get("porcentaje", 0)),
            "zona": _fix_text(row.get("zona", "")),
        }
        for row in _table_rows(gastos_payload, "conf_gastosfamiliares")
    ]
    regresion = [
        {
            "parametro": _fix_text(row.get("parametro", "")),
            "nivel": _fix_text(row.get("nivel", "")),
            "estimacion": _as_float(row.get("estimacion", 0)),
        }
        for row in _table_rows(regresion_payload, "configuraciones_configuracion")
    ]
    tasas = []
    for row in _table_rows(tasas_payload, "configuraciones_tasainteres"):
        tasas.append(
            {
                "linea_credito": _normalize_rate_text(row.get("linea_credito", "")),
                "forma_pago": _normalize_rate_text(row.get("forma_pago", "")),
                "sub_categoria": _normalize_rate_text(row.get("sub_categoria", row.get("subcategoria", "General"))) or "General",
                "categoria_riesgo": _fix_text(row.get("categoria_riesgo", "NA")) or "NA",
                "tasa_ea": _as_float(row.get("tasa_ea", 0)),
            }
        )
    return {
        "agencias": agencias,
        "gastos_familiares": gastos_familiares,
        "regresion": regresion,
        "tasas": tasas,
    }


class Command(BaseCommand):
    help = "Carga configuracion de XCORE Consumo desde JSON limpios o exports legacy con upsert idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Ruta a archivo JSON unificado con regresion, gastos, agencias y tasas.")
        parser.add_argument("--agencias-file", help="Ruta a agencias.json o agencias_clean.json.")
        parser.add_argument("--gastos-file", help="Ruta a conf_gastosfamiliares.json o conf_gastosfamiliares_clean.json.")
        parser.add_argument("--regresion-file", help="Ruta a configuraciones_configuracion.json o configuraciones_configuracion_clean.json.")
        parser.add_argument("--tasas-file", help="Ruta a configuraciones_tasainteres.json o configuraciones_tasainteres_clean.json.")

    def handle(self, *args, **options):
        project_root = Path(settings.BASE_DIR).resolve().parent
        if options.get("file"):
            path = Path(options["file"]).expanduser()
            if not path.exists():
                raise CommandError(f"No existe el archivo: {path}")
            payload = _load_json(path)
        else:
            payload = _build_unified_payload(
                project_root,
                agencias_file=options.get("agencias_file"),
                gastos_file=options.get("gastos_file"),
                regresion_file=options.get("regresion_file"),
                tasas_file=options.get("tasas_file"),
            )

        regresion_count = 0
        gastos_count = 0
        agencias_count = 0
        tasas_count = 0

        for row in payload.get("regresion", []):
            if not row["parametro"] or not row["nivel"]:
                continue
            ConfiguracionRegresion.objects.update_or_create(
                parametro=row["parametro"],
                nivel=row["nivel"],
                defaults={"estimacion": row["estimacion"]},
            )
            regresion_count += 1
        for row in payload.get("gastos_familiares", []):
            ConfiguracionGastosFamiliares.objects.update_or_create(
                salario_minimo=row["salario_minimo"],
                cant_personasacargo=row["cant_personasacargo"],
                zona=row.get("zona"),
                defaults={"porcentaje": row["porcentaje"]},
            )
            gastos_count += 1
        for row in payload.get("agencias", []):
            if not row["canal"]:
                continue
            ConfiguracionAgenciaCanal.objects.update_or_create(
                canal=row["canal"],
                defaults={"codigo": row.get("codigo", ""), "puntos": row.get("puntos", 0)},
            )
            agencias_count += 1
        for row in payload.get("tasas", []):
            if not row["linea_credito"] or not row["forma_pago"]:
                continue
            TasaInteresConsumo.objects.update_or_create(
                linea_credito=row["linea_credito"],
                forma_pago=row["forma_pago"],
                sub_categoria=row.get("sub_categoria", "General"),
                categoria_riesgo=row.get("categoria_riesgo", "NA"),
                defaults={"tasa_ea": row["tasa_ea"]},
            )
            tasas_count += 1
        self.stdout.write(
            self.style.SUCCESS(
                "Configuracion de consumo cargada/actualizada. "
                f"Regresion={regresion_count}, Gastos={gastos_count}, Agencias={agencias_count}, Tasas={tasas_count}"
            )
        )
