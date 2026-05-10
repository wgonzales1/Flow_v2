# tomtom-collector

Colecta datos de tráfico de TomTom (Flow, Incidents, Routes) cada hora entre las 7am y 22pm (Santiago, Chile).  
Los datos se guardan en **Supabase** (PostgreSQL). Las tablas se crean automáticamente si no existen.

---

## Estructura del repo

```
tomtom-collector/
├── common/
│   ├── db.py          # Conexión + auto-creación de tablas + upsert genérico
│   └── utils.py       # Logging, ventana horaria, carga de config
├── flow/
│   ├── collector.py   # Flow API
│   └── requirements.txt
├── incidents/
│   ├── collector.py   # Incidents API
│   └── requirements.txt
├── routes/
│   ├── collector.py   # Calculate Route API
│   └── requirements.txt
├── config/
│   └── points.json    # Puntos y bboxes a consultar (editar esto)
├── .env.example
└── .gitignore
```

---

## Setup en Railway

### 1. Crear 3 servicios apuntando al mismo repo

En Railway → New Project → Deploy from GitHub repo → seleccionar este repo.  
Repetir 3 veces (uno por cada servicio: flow, incidents, routes).

Para cada servicio, configurar el **Root Directory**:
- Servicio flow      → `flow`
- Servicio incidents → `incidents`
- Servicio routes    → `routes`

Y el **Start Command**:
```
python collector.py
```

### 2. Variables de entorno por servicio

| Variable       | Valor                                      |
|----------------|--------------------------------------------|
| `TOMTOM_API_KEY` | API key TomTom (distinta por servicio)   |
| `DATABASE_URL`   | Connection string de Supabase (igual para los 3) |

La `DATABASE_URL` la copiás desde:  
**Supabase → Settings → Database → Connection string → URI**

### 3. Cron Job en Railway

En cada servicio: **Settings → Cron Schedule**

```
0 * * * *
```

Esto dispara cada hora. La lógica de ventana 7am–22pm está **dentro del script**
(ver `common/utils.py → check_time_window`) para manejar correctamente el cambio
de horario de Chile (UTC-3 / UTC-4).

---

## Editar puntos a consultar

Modificar `config/points.json`:

- **`flow_points`** → lista de coordenadas donde consultar velocidad
- **`incidents_bboxes`** → bounding boxes donde buscar incidentes  
- **`routes`** → pares origen-destino para tiempos de viaje

Usar el [map picker](../map_picker.html) para obtener coordenadas fácilmente.

---

## Correr localmente

```bash
cp .env.example .env
# editar .env con tus claves

# instalar dependencias (desde la carpeta del servicio)
cd flow && pip install -r requirements.txt

# correr
python collector.py
```

---

## Exportar datos para entrenar modelo

Desde Python (local o Colab):

```python
from sqlalchemy import create_engine
import pandas as pd
import os

engine = create_engine(os.getenv("DATABASE_URL"))

flow      = pd.read_sql("SELECT * FROM tomtom_flow      ORDER BY requested_at",      engine)
incidents = pd.read_sql("SELECT * FROM tomtom_incidents ORDER BY requested_at",      engine)
routes    = pd.read_sql("SELECT * FROM tomtom_routes    ORDER BY requested_depart_at", engine)

flow.to_parquet("flow.parquet", index=False)
incidents.to_parquet("incidents.parquet", index=False)
routes.to_parquet("routes.parquet", index=False)
```
