"""Inicio de sesión con Google Workspace.

El mismo flujo sirve para la web y para la app de escritorio: la app de escritorio
abre el navegador en /auth/login?client=desktop&port=N y recibe el token de sesión
en http://127.0.0.1:N/callback.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
]

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
fernet = Fernet(settings.fernet_key)


def encrypt(value: str) -> str:
    return fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return fernet.decrypt(value.encode()).decode()


def _sign(payload: dict, minutes: int) -> str:
    payload = {**payload, "exp": datetime.now(timezone.utc) + timedelta(minutes=minutes)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _redirect_uri() -> str:
    return f"{settings.backend_url}/auth/callback"


@router.get("/login")
def login(client: str = Query("web", pattern="^(web|desktop)$"), port: int | None = None):
    if client == "desktop" and not (port and 1024 <= port <= 65535):
        raise HTTPException(400, "La app de escritorio debe indicar un puerto local válido")
    state = _sign({"client": client, "port": port, "purpose": "oauth_state"}, minutes=10)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "hd": settings.allowed_domain,
        "state": state,
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
def callback(code: str, state: str, db: Session = Depends(get_db)):
    try:
        st = jwt.decode(state, settings.secret_key, algorithms=["HS256"])
        assert st.get("purpose") == "oauth_state"
    except Exception:
        raise HTTPException(400, "Estado de inicio de sesión inválido o expirado")

    resp = httpx.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(400, "Google rechazó el código de autorización")
    tokens = resp.json()

    info = id_token.verify_oauth2_token(tokens["id_token"], google_requests.Request(), settings.google_client_id)
    # El parámetro hd del login es solo una sugerencia; aquí se valida de verdad
    if info.get("hd") != settings.allowed_domain or not info.get("email_verified"):
        raise HTTPException(403, f"Solo se permiten cuentas de {settings.allowed_domain}")

    user = db.scalar(select(User).where(User.email == info["email"]))
    if user is None:
        user = User(email=info["email"])
        db.add(user)
    user.name = info.get("name", "")
    user.picture = info.get("picture")
    if tokens.get("refresh_token"):
        user.refresh_token_enc = encrypt(tokens["refresh_token"])
    db.commit()

    session_token = _sign({"sub": str(user.id), "purpose": "session"}, minutes=settings.session_hours * 60)
    if st["client"] == "desktop":
        return RedirectResponse(f"http://127.0.0.1:{st['port']}/callback?token={session_token}")
    return RedirectResponse(f"{settings.frontend_url}/#token={session_token}")


_bearer = HTTPBearer()


def current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    try:
        payload = jwt.decode(creds.credentials, settings.secret_key, algorithms=["HS256"])
        assert payload.get("purpose") == "session"
    except Exception:
        raise HTTPException(401, "Sesión inválida o expirada")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "Usuario no encontrado")
    return user
