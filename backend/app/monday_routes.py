"""Conexión de cada persona con su propia cuenta de Monday y elección de su tablero.

Cada quien conecta su cuenta pegando su token personal de Monday (o, si la empresa registró una app OAuth,
autorizándola con un botón). El token solo ve los tableros a los que esa persona tiene acceso, se guarda
cifrado y nunca se devuelve al navegador. Los acuerdos aparecen en Monday como creados por ella.
"""

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .auth import _sign, current_user
from .config import get_settings
from .db import get_db
from .models import MondayConnection, User
from .schemas import MondayTablero, MondayTableroElegir, MondayToken, UserOut
from .services import monday

router = APIRouter(tags=["monday"])


def _require_enabled() -> None:
    if not get_settings().monday_enabled:
        raise HTTPException(503, "La integración con Monday no está configurada")


def _save_connection(db: Session, user: User, token: str, account: dict) -> None:
    name = account.get("name") or account.get("email") or ""
    conn = user.monday
    if conn is None:
        conn = user.monday = MondayConnection(user_id=user.id)
    elif conn.account_name != name:
        # Otra cuenta de Monday: el tablero elegido puede no ser suyo
        conn.board_id = conn.board_name = conn.board_url = None
    conn.token = token
    conn.account_name = name
    db.commit()


def connection(user: User) -> MondayConnection:
    """Conexión de Monday de la persona; error claro si no la tiene."""
    _require_enabled()
    if user.monday is None:
        raise HTTPException(409, "Primero conecta tu cuenta de Monday en tu perfil")
    return user.monday


def monday_call(fn, *args, db: Session | None = None, user: User | None = None):
    """Llama a Monday y traduce sus errores. Si el token ya no sirve, se borra la conexión para pedir reconectar."""
    try:
        return fn(*args)
    except monday.MondayAuthError as exc:
        if db is not None and user is not None and user.monday is not None:
            db.delete(user.monday)
            db.commit()
        raise HTTPException(409, str(exc))  # 401 cerraría la sesión del portal; solo hay que reconectar Monday
    except monday.MondayError as exc:
        raise HTTPException(502, str(exc))


@router.get("/api/monday/conectar")
def connect(user: User = Depends(current_user)):
    """URL de autorización de Monday. El navegador la abre y Monday regresa a /auth/monday/callback."""
    if not get_settings().monday_oauth:
        raise HTTPException(503, "No hay app de Monday registrada; conecta tu cuenta pegando tu token personal")
    state = _sign({"sub": str(user.id), "purpose": "monday_state"}, minutes=10)
    return {"url": monday.authorize_url(state)}


@router.get("/auth/monday/callback")
def callback(code: str | None = None, state: str = "", error: str | None = None, db: Session = Depends(get_db)):
    back = f"{get_settings().frontend_url.rstrip('/')}/#/perfil"
    try:
        st = jwt.decode(state, get_settings().secret_key, algorithms=["HS256"])
        assert st.get("purpose") == "monday_state"
    except Exception:
        return RedirectResponse(f"{back}?monday=expirado")
    if error or not code:
        return RedirectResponse(f"{back}?monday=cancelado")
    user = db.get(User, int(st["sub"]))
    if user is None:
        return RedirectResponse(f"{back}?monday=error")
    try:
        token = monday.exchange_code(code)
        account = monday.me(token)
    except monday.MondayError:
        return RedirectResponse(f"{back}?monday=error")

    _save_connection(db, user, token, account)
    return RedirectResponse(f"{back}?monday=conectado")


@router.put("/api/monday/token", response_model=UserOut)
def save_token(body: MondayToken, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Conecta la cuenta de Monday con el token personal de la persona; antes de guardarlo se prueba contra Monday."""
    from .routes import _user_out

    _require_enabled()
    token = body.token.strip()
    try:
        account = monday.me(token)
    except monday.MondayAuthError:
        raise HTTPException(422, "Monday no aceptó ese token. Cópialo de nuevo desde Monday y revisa que esté completo.")
    except monday.MondayError as exc:
        raise HTTPException(502, str(exc))
    _save_connection(db, user, token, account)
    db.refresh(user)
    return _user_out(user)


@router.delete("/api/monday", response_model=UserOut)
def disconnect(user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .routes import _user_out

    if user.monday is not None:
        db.delete(user.monday)
        db.commit()
        db.refresh(user)
    return _user_out(user)


@router.get("/api/monday/tableros", response_model=list[MondayTablero])
def boards(user: User = Depends(current_user), db: Session = Depends(get_db)):
    conn = connection(user)
    return [
        MondayTablero(id=b.id, nombre=b.name, espacio=b.workspace, url=b.url)
        for b in monday_call(monday.list_boards, conn.token, db=db, user=user)
    ]


@router.put("/api/monday/tablero", response_model=UserOut)
def choose_board(body: MondayTableroElegir, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from .routes import _user_out

    conn = connection(user)
    board = monday_call(monday.board_info, conn.token, body.id, db=db, user=user)  # confirma que tiene acceso
    conn.board_id, conn.board_name, conn.board_url = board.id, board.name, board.url
    db.commit()
    return _user_out(user)
