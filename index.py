# Punto de entrada para Vercel: carga la API de backend/app. Localmente se sigue usando
#   cd backend && python -m uvicorn app.main:app --port 8000
from backend.app.main import app  # noqa: F401
