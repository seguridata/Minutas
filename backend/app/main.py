import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import admin, auth, monday_routes, routes
from .config import get_settings
from .db import Base, engine
from .models import add_missing_columns, encrypt_legacy_rows

logging.basicConfig(level=logging.INFO)

# Para producción conviene migrar con Alembic; para arrancar basta con crear las tablas
Base.metadata.create_all(engine)
add_missing_columns(engine)
# Solo las bases SQLite viejas pueden tener filas sin cifrar; en Postgres se evita recorrerlo todo en cada arranque
if engine.dialect.name == "sqlite" and (n := encrypt_legacy_rows(engine)):
    logging.getLogger(__name__).info("Se cifraron %s reuniones guardadas sin cifrar", n)

app = FastAPI(title="Minutas de reuniones")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_url],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router)
app.include_router(routes.router)
app.include_router(admin.router)
app.include_router(monday_routes.router)


@app.get("/health")
def health():
    return {"ok": True}


# En el servidor el mismo proceso sirve el portal ya compilado (frontend/dist), así todo sale por un
# solo puerto. Va al final para que las rutas de la API tengan prioridad.
_portal = Path(os.environ.get("PORTAL_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if (_portal / "index.html").exists():
    app.mount("/", StaticFiles(directory=_portal, html=True), name="portal")
