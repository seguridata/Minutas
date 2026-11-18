import json
import os
import sys
from pathlib import Path

# Con PyInstaller los recursos quedan en sys._MEIPASS
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

# El instalador trae fija la dirección del servidor (build.ps1 escribe server.json); las variables
# de entorno tienen prioridad para pruebas
_server_file = BUNDLE / "minutas" / "server.json"
_server = json.loads(_server_file.read_text(encoding="utf-8")) if _server_file.exists() else {}

BACKEND_URL = os.getenv("MINUTAS_BACKEND_URL", _server.get("backend_url", "http://localhost:8000")).rstrip("/")
WEB_URL = os.getenv("MINUTAS_WEB_URL", _server.get("web_url", "http://localhost:5173")).rstrip("/")

# tiny | base | small | medium | large-v3  (más grande = más preciso y más lento)
# El instalador incluye el modelo small, así no se descarga en cada equipo
_bundled_model = BUNDLE / "models" / "whisper-small"
WHISPER_MODEL = os.getenv("MINUTAS_WHISPER_MODEL") or (str(_bundled_model) if _bundled_model.exists() else "small")
WHISPER_DEVICE = os.getenv("MINUTAS_WHISPER_DEVICE", "auto")  # auto | cpu | cuda

COMPANY = os.getenv("MINUTAS_EMPRESA", "SeguriData")
KEYRING_SERVICE = "minutas-reuniones"


def consent_notice(user_name: str) -> str:
    return (
        f"Aviso: {user_name} está usando el asistente de minutas de {COMPANY} para transcribir esta "
        "reunión y enviar por correo la minuta con los acuerdos a los asistentes. El audio no se guarda; "
        "solo se conserva la transcripción. Si alguien no está de acuerdo, por favor dígalo ahora y "
        "no se grabará."
    )
