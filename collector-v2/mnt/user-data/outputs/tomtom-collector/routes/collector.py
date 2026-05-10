"""
routes/collector.py
-------------------
Colecta tiempos de viaje de TomTom Calculate Route API cada hora (7am–22pm Santiago).
Guarda en tabla tomtom_routes en Supabase.

Variables de entorno (Railway):
  TOMTOM_API_KEY   → API key exclusiva para este servicio
  DATABASE_URL     → Connection string de Supabase
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from datetime import datetime
from common.db    import get_engine, ensure_tables, upsert_records
from common.utils import setup_logging, check_time_window, load_config, TIMEZONE

log      = setup_logging("routes")
API_KEY  = os.getenv("TOMTOM_API_KEY")
BASE_URL = "https://api.tomtom.com/routing/1/calculateRoute"
TABLE    = "tomtom_routes"


# ─────────────────────────────────────────────
# API
# ─────────────────────────────────────────────

def call_route(origin: tuple, destination: tuple, depart_at: datetime) -> dict | None:
    origin_str = f"{origin[0]},{origin[1]}"
    dest_str   = f"{destination[0]},{destination[1]}"
    locations  = f"{origin_str}:{dest_str}"
    depart_str = depart_at.strftime("%Y-%m-%dT%H:%M:%S")

    params = {
        "key":                  API_KEY,
        "travelMode":           "car",
        "routeType":            "fastest",
        "traffic":              "true",
        "departAt":             depart_str,
        "computeTravelTimeFor": "all",
    }

    url = f"{BASE_URL}/{locations}/json"
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        log.warning("HTTP %s para %s → %s", e.response.status_code, origin_str, dest_str)
    except requests.exceptions.Timeout:
        log.warning("Timeout para %s → %s", origin_str, dest_str)
    except requests.exceptions.RequestException as e:
        log.error("Error de red: %s", e)
    return None


def parse_route(response: dict, route: dict, requested_at: datetime) -> dict | None:
    try:
        summary = response["routes"][0]["summary"]

        travel_time_s   = summary["travelTimeInSeconds"]
        traffic_delay_s = summary.get("trafficDelayInSeconds", 0)
        length_m        = summary["lengthInMeters"]
        no_traffic_s    = summary.get("noTrafficTravelTimeInSeconds")
        historic_s      = summary.get("historicTrafficTravelTimeInSeconds")
        live_s          = summary.get("liveTrafficIncidentsTravelTimeInSeconds")

        dep_str = summary.get("departureTime", "")
        arr_str = summary.get("arrivalTime", "")
        dep_time = datetime.fromisoformat(dep_str) if dep_str else None
        arr_time = datetime.fromisoformat(arr_str) if arr_str else None

        congestion_ratio = (
            round(travel_time_s / no_traffic_s, 4)
            if no_traffic_s and no_traffic_s > 0 else None
        )

        return {
            "route_name":               route["name"],
            "origin_lat":               route["origin_lat"],
            "origin_lon":               route["origin_lon"],
            "dest_lat":                 route["dest_lat"],
            "dest_lon":                 route["dest_lon"],
            "requested_depart_at":      requested_at,
            "travel_mode":              "car",
            "route_type":               "fastest",
            "travel_time_s":            travel_time_s,
            "length_m":                 length_m,
            "hour_of_day":              requested_at.hour,
            "day_of_week":              requested_at.weekday(),
            "is_weekend":               int(requested_at.weekday() >= 5),
            "month":                    requested_at.month,
            "no_traffic_time_s":        no_traffic_s,
            "historic_time_s":          historic_s,
            "congestion_ratio":         congestion_ratio,
            "traffic_delay_s":          traffic_delay_s,
            "live_traffic_time_s":      live_s,
            "historic_vs_live_delta_s": (travel_time_s - historic_s) if historic_s else None,
            "api_departure_time":       dep_time,
            "api_arrival_time":         arr_time,
            "collected_at":             datetime.now(TIMEZONE),
        }
    except (KeyError, IndexError, TypeError) as e:
        log.warning("Error parseando ruta '%s': %s", route.get("name"), e)
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

    for route in config["routes"]:
        log.info("Consultando ruta: %s", route["name"])
        origin      = (route["origin_lat"], route["origin_lon"])
        destination = (route["dest_lat"],   route["dest_lon"])

        response = call_route(origin, destination, now)
        if response:
            record = parse_route(response, route, now)
            if record:
                records.append(record)

        time.sleep(0.5)

    upsert_records(engine, TABLE, records, conflict_cols=["route_name", "requested_depart_at"])
    log.info("Routes — ciclo completado. %d registros guardados.", len(records))
