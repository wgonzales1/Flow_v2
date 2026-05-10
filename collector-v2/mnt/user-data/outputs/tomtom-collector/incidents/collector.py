"""
incidents/collector.py
----------------------
Colecta incidentes de TomTom Traffic Incidents API cada hora (7am–22pm Santiago).
Guarda en tabla tomtom_incidents en Supabase.

Variables de entorno (Railway):
  TOMTOM_API_KEY   → API key exclusiva para este servicio
  DATABASE_URL     → Connection string de Supabase
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from common.db    import get_engine, ensure_tables, upsert_records
from common.utils import setup_logging, check_time_window, load_config, TIMEZONE

log      = setup_logging("incidents")
API_KEY  = os.getenv("TOMTOM_API_KEY")
BASE_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
TABLE    = "tomtom_incidents"

# Tipos de incidente TomTom → texto legible
INCIDENT_TYPES = {
    0: "UNKNOWN",
    1: "ACCIDENT",
    2: "FOG",
    3: "DANGEROUS_CONDITIONS",
    4: "RAIN",
    5: "ICE",
    6: "JAM",
    7: "LANE_CLOSED",
    8: "ROAD_CLOSED",
    9: "ROAD_WORKS",
    10: "WIND",
    11: "FLOODING",
    14: "BROKEN_DOWN_VEHICLE",
}


# ─────────────────────────────────────────────
# API
# ─────────────────────────────────────────────

def call_incidents(bbox: dict) -> dict | None:
    """
    Consulta incidentes dentro de un bounding box.
    bbox = { name, min_lat, min_lon, max_lat, max_lon }
    """
    # TomTom espera: minLon,minLat,maxLon,maxLat
    bbox_str = f"{bbox['min_lon']},{bbox['min_lat']},{bbox['max_lon']},{bbox['max_lat']}"

    params = {
        "key":      API_KEY,
        "bbox":     bbox_str,
        "fields":   "{incidents{type,geometry,properties}}",
        "language": "es-ES",
        "timeValidityFilter": "present",
    }
    try:
        r = requests.get(BASE_URL, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        log.warning("HTTP %s para bbox '%s'", e.response.status_code, bbox["name"])
    except requests.exceptions.Timeout:
        log.warning("Timeout para bbox '%s'", bbox["name"])
    except requests.exceptions.RequestException as e:
        log.error("Error de red: %s", e)
    return None


def parse_incidents(response: dict, bbox: dict, requested_at: datetime) -> list[dict]:
    records = []
    incidents = response.get("incidents", [])

    if not incidents:
        log.info("Sin incidentes en '%s'.", bbox["name"])
        return records

    for incident in incidents:
        try:
            props = incident.get("properties", {})
            geom  = incident.get("geometry", {})

            # Coordenadas — geometry puede ser Point o LineString
            coords = geom.get("coordinates", [])
            if geom.get("type") == "LineString" and coords:
                start_lon, start_lat = coords[0][0], coords[0][1]
                end_lon,   end_lat   = coords[-1][0], coords[-1][1]
            elif geom.get("type") == "Point" and coords:
                start_lon, start_lat = coords[0], coords[1]
                end_lon,   end_lat   = None, None
            else:
                start_lat = start_lon = end_lat = end_lon = None

            # Tiempos
            start_str = props.get("startTime")
            end_str   = props.get("endTime")
            start_time = datetime.fromisoformat(start_str) if start_str else None
            end_time   = datetime.fromisoformat(end_str)   if end_str   else None

            # Longitud del incidente
            length_m = props.get("length")
            if length_m is not None:
                length_m = int(length_m)

            records.append({
                "bbox_name":     bbox["name"],
                "requested_at":  requested_at,
                "incident_id":   props.get("id"),
                "incident_type": INCIDENT_TYPES.get(props.get("iconCategory", 0), "UNKNOWN"),
                "magnitude":     props.get("magnitudeOfDelay"),
                "description":   props.get("events", [{}])[0].get("description") if props.get("events") else None,
                "cause":         props.get("events", [{}])[0].get("cause")       if props.get("events") else None,
                "start_lat":     start_lat,
                "start_lon":     start_lon,
                "end_lat":       end_lat,
                "end_lon":       end_lon,
                "start_time":    start_time,
                "end_time":      end_time,
                "delay_seconds": props.get("delay"),
                "road_numbers":  ", ".join(props.get("roadNumbers", [])),
                "length_m":      length_m,
                "hour_of_day":   requested_at.hour,
                "day_of_week":   requested_at.weekday(),
                "is_weekend":    int(requested_at.weekday() >= 5),
                "month":         requested_at.month,
            })
        except Exception as e:
            log.warning("Error parseando incidente: %s", e)
            continue

    return records


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    now    = check_time_window(start_hour=7, end_hour=22)
    engine = get_engine()
    ensure_tables(engine, [TABLE])
    config = load_config()

    all_records = []

    for bbox in config["incidents_bboxes"]:
        log.info("Consultando incidentes: %s", bbox["name"])
        response = call_incidents(bbox)

        if response:
            records = parse_incidents(response, bbox, now)
            all_records.extend(records)
            log.info("  → %d incidentes encontrados.", len(records))

        time.sleep(0.3)

    # Para incidents: el conflict es (incident_id, requested_at)
    # Si incident_id puede ser None (raro), filtramos esos registros
    valid = [r for r in all_records if r.get("incident_id")]
    upsert_records(engine, TABLE, valid, conflict_cols=["incident_id", "requested_at"])
    log.info("Incidents — ciclo completado. %d registros guardados.", len(valid))
