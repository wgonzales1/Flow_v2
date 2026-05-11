"""
common/db.py
------------
Conexión compartida a Supabase (PostgreSQL) y auto-creación de tablas.
Solo necesitás definir DATABASE_URL en las variables de entorno.
"""

import os
import logging
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────

def get_engine():
    """
    Crea el engine de SQLAlchemy apuntando a Supabase.
    Supabase entrega la URL en formato postgresql://, pero por si acaso
    también corregimos el prefijo viejo 'postgres://'.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise EnvironmentError(
            "DATABASE_URL no está definida. "
            "Copiala desde Supabase → Settings → Database → Connection string (URI)."
        )
    url = url.replace("postgres://", "postgresql://", 1)
    return create_engine(url, pool_pre_ping=True)


# ─────────────────────────────────────────────
# DDL — una función por tabla
# ─────────────────────────────────────────────

FLOW_DDL = """
CREATE TABLE IF NOT EXISTS tomtom_flow (
    id                  BIGSERIAL PRIMARY KEY,

    -- Identificación del punto
    point_name          TEXT NOT NULL,
    lat                 DOUBLE PRECISION NOT NULL,
    lon                 DOUBLE PRECISION NOT NULL,
    requested_at        TIMESTAMPTZ NOT NULL,

    -- Datos de tráfico
    freeflow_speed      INTEGER,          -- km/h sin tráfico
    current_speed       INTEGER,          -- km/h en el momento
    current_travel_time INTEGER,          -- segundos
    freeflow_travel_time INTEGER,         -- segundos
    confidence          DOUBLE PRECISION, -- 0-1, fiabilidad del dato
    road_closure        BOOLEAN,

    -- Features derivadas
    speed_ratio         DOUBLE PRECISION, -- current/freeflow (congestion proxy)
    hour_of_day         SMALLINT,
    day_of_week         SMALLINT,
    is_weekend          SMALLINT,
    month               SMALLINT,

    collected_at        TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (point_name, requested_at)
);
"""

INCIDENTS_DDL = """
CREATE TABLE IF NOT EXISTS tomtom_incidents (
    id                  BIGSERIAL PRIMARY KEY,

    -- Identificación de la consulta
    bbox_name           TEXT NOT NULL,    -- nombre de la zona consultada
    requested_at        TIMESTAMPTZ NOT NULL,

    -- Datos del incidente
    incident_id         TEXT,
    incident_type       TEXT,             -- ACCIDENT, JAM, ROAD_WORK, etc.
    magnitude           SMALLINT,         -- 0-4 severidad
    description         TEXT,
    cause               TEXT,

    -- Geometría (punto de inicio)
    start_lat           DOUBLE PRECISION,
    start_lon           DOUBLE PRECISION,
    end_lat             DOUBLE PRECISION,
    end_lon             DOUBLE PRECISION,

    -- Tiempos
    start_time          TIMESTAMPTZ,
    end_time            TIMESTAMPTZ,
    delay_seconds       INTEGER,
    road_numbers        TEXT,             -- "AV. APOQUINDO, COSTANERA"
    length_m            INTEGER,

    -- Features derivadas
    hour_of_day         SMALLINT,
    day_of_week         SMALLINT,
    is_weekend          SMALLINT,
    month               SMALLINT,

    collected_at        TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (incident_id, requested_at)
);
"""

ROUTES_DDL = """
CREATE TABLE IF NOT EXISTS tomtom_routes (
    id                          BIGSERIAL PRIMARY KEY,

    route_name                  TEXT NOT NULL,
    origin_lat                  DOUBLE PRECISION,
    origin_lon                  DOUBLE PRECISION,
    dest_lat                    DOUBLE PRECISION,
    dest_lon                    DOUBLE PRECISION,
    requested_depart_at         TIMESTAMPTZ NOT NULL,
    travel_mode                 TEXT,
    route_type                  TEXT,

    -- TARGET para el modelo
    travel_time_s               INTEGER,

    -- Features
    length_m                    INTEGER,
    hour_of_day                 SMALLINT,
    day_of_week                 SMALLINT,
    is_weekend                  SMALLINT,
    month                       SMALLINT,
    no_traffic_time_s           INTEGER,
    historic_time_s             INTEGER,
    congestion_ratio            DOUBLE PRECISION,

    -- Informativos (no usar como features: data leakage)
    traffic_delay_s             INTEGER,
    live_traffic_time_s         INTEGER,
    historic_vs_live_delta_s    INTEGER,

    api_departure_time          TIMESTAMPTZ,
    api_arrival_time            TIMESTAMPTZ,

    collected_at                TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (route_name, requested_depart_at)
);
"""

TABLE_DDLS = {
    "tomtom_flow":      FLOW_DDL,
    "tomtom_incidents": INCIDENTS_DDL,
    "tomtom_routes":    ROUTES_DDL,
}


def ensure_tables(engine, tables: list[str]) -> None:
    """
    Recibe una lista de nombres de tabla y crea las que no existan.
    Ejemplo:
        ensure_tables(engine, ["tomtom_flow"])
        ensure_tables(engine, ["tomtom_incidents"])
        ensure_tables(engine, ["tomtom_routes"])
    """
    with engine.connect() as conn:
        for table in tables:
            if table not in TABLE_DDLS:
                raise ValueError(f"No hay DDL definido para la tabla '{table}'.")
            conn.execute(text(TABLE_DDLS[table]))
            conn.commit()
            log.info("Tabla '%s' lista (creada o ya existía).", table)


# ─────────────────────────────────────────────
# Upsert genérico
# ─────────────────────────────────────────────

def upsert_records(engine, table: str, records: list[dict], conflict_cols: list[str]) -> None:
    """
    Inserta registros con ON CONFLICT DO UPDATE.

    - table:          nombre de la tabla destino
    - records:        lista de dicts con los datos
    - conflict_cols:  columnas que forman la clave única (para el ON CONFLICT)
    """
    if not records:
        log.info("Sin registros para insertar en '%s'.", table)
        return

    import pandas as pd
    df = pd.DataFrame(records)

    cols         = list(df.columns)
    cols_str     = ", ".join(cols)
    placeholders = ", ".join([f":{c}" for c in cols])
    conflict_str = ", ".join(conflict_cols)
    update_cols  = [c for c in cols if c not in conflict_cols]
    updates_str  = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])

    sql = text(f"""
        INSERT INTO {table} ({cols_str})
        VALUES ({placeholders})
        ON CONFLICT ({conflict_str})
        DO UPDATE SET {updates_str}
    """)

    with engine.begin() as conn:
        conn.execute(sql, df.to_dict(orient="records"))

    log.info("Upsert de %d registros en '%s' completado.", len(records), table)
