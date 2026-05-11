"""
flow/collector.py
-----------------
Colecta datos de TomTom Traffic Flow API cada hora (7am–22pm Santiago).
Guarda en tabla tomtom_flow en Supabase.

Variables de entorno (Railway):
  TOMTOM_API_KEY   → API key exclusiva para este servicio
  DATABASE_URL     → Connection string de Supabase
"""

import os
import sys
import time
from pathlib import Path

# ── Permite importar 'common' estando en flow/ ──────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from common.db   import get_engine, ensure_tables, upsert_records
from common.utils import setup_logging, check_time_window, load_config

log        = setup_logging("flow")
API_KEY    = os.getenv("TOMTOM_API_KEY")
BASE_URL   = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
TABLE      = "tomtom_flow"


# ─────────────────────────────────────────────
# API
# ─────────────────────────────────────────────

def call_flow(lat: float, lon: float) -> dict | None:
    """
    Consulta Flow para un punto dado.
    El endpoint /absolute devuelve velocidades actuales + freeflow.
    """
    params = {
        "key":   API_KEY,
        "point": f"{lat},{lon}",
        "unit":  "KMPH",
    }
    try:
        r = requests.get(BASE_URL, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        log.warning("HTTP %s para (%.4f, %.4f)", e.response.status_code, lat, lon)
    except requests.exceptions.Timeout:
        log.warning("Timeout para (%.4f, %.4f)", lat, lon)
    except requests.exceptions.RequestException as e:
        log.error("Error de red: %s", e)
    return None


def parse_flow(response: dict, point: dict, requested_at) -> dict | None:
    try:
        fd = response["flowSegmentData"]

        freeflow_speed       = fd.get("freeFlowSpeed")
        current_speed        = fd.get("currentSpeed")
        current_travel_time  = fd.get("currentTravelTime")
        freeflow_travel_time = fd.get("freeFlowTravelTime")
        confidence           = fd.get("confidence")
        road_closure         = fd.get("roadClosure", False)

        speed_ratio = (
            round(current_speed / freeflow_speed, 4)
            if freeflow_speed and freeflow_speed > 0 else None
        )

        return {
            "point_name":           point["name"],
            "lat":                  point["lat"],
            "lon":                  point["lon"],
            "requested_at":         requested_at,
            "freeflow_speed":       freeflow_speed,
            "current_speed":        current_speed,
            "current_travel_time":  current_travel_time,
            "freeflow_travel_time": freeflow_travel_time,
            "confidence":           confidence,
            "road_closure":         road_closure,
            "speed_ratio":          speed_ratio,
            "hour_of_day":          requested_at.hour,
            "day_of_week":          requested_at.weekday(),
            "is_weekend":           int(requested_at.weekday() >= 5),
            "month":                requested_at.month,
        }
    except (KeyError, TypeError) as e:
        log.warning("Error parseando flow para '%s': %s", point["name"], e)
        return None


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    now    = check_time_window(start_hour=7, end_hour=22)
    engine = get_engine()
    ensure_tables(engine, [TABLE])
    config = load_config()

    records = []

    for point in config["flow_points"]:
        log.info("Consultando flow: %s (%.4f, %.4f)", point["name"], point["lat"], point["lon"])
        response = call_flow(point["lat"], point["lon"])

        if response:
            record = parse_flow(response, point, now)
            if record:
                records.append(record)

        time.sleep(0.3)

    upsert_records(engine, TABLE, records, conflict_cols=["point_name", "requested_at"])
    log.info("Flow — ciclo completado. %d registros guardados.", len(records))
