"""
common/utils.py
---------------
Utilidades compartidas: ventana horaria, logging, carga de config.
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/Santiago")


def setup_logging(service_name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{service_name}] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(service_name)


def check_time_window(start_hour: int = 7, end_hour: int = 22) -> datetime:
    """
    Retorna el datetime actual (Santiago) si está dentro de la ventana.
    Si está fuera, loguea y hace sys.exit(0) — Railway no marca esto como error.

    Esto es más robusto que el cron de Railway porque:
    - Chile cambia de horario (UTC-3 / UTC-4), el cron no sabe eso.
    - El script decide por sí mismo si debe correr.
    """
    now = datetime.now(TIMEZONE).replace(minute=0, second=0, microsecond=0)
    if not (start_hour <= now.hour < end_hour):
        logging.info(
            "Hora actual %s fuera de ventana %d:00–%d:00. Saliendo.",
            now.strftime("%H:%M"), start_hour, end_hour,
        )
        sys.exit(0)
    return now


def load_config(config_path: str = None) -> dict:
    """
    Carga config/points.json desde la raíz del repo.
    Cada servicio puede override el path si quiere.
    """
    if config_path is None:
        # Sube dos niveles desde common/ hasta la raíz del repo
        root = Path(__file__).parent.parent
        config_path = root / "config" / "points.json"

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
