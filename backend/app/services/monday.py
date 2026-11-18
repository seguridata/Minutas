"""Envío de los acuerdos de una minuta a un tablero de Monday (API GraphQL)."""

import json
import re
import unicodedata
import logging
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from ..config import get_settings
from ..schemas import Minuta

log = logging.getLogger(__name__)

API_URL = "https://api.monday.com/v2"
AUTH_URL = "https://auth.monday.com/oauth2/authorize"
TOKEN_URL = "https://auth.monday.com/oauth2/token"
# Permisos que pide la app: ver y escribir tableros, y buscar personas por correo para asignar responsables
SCOPES = "me:read boards:read boards:write users:read notifications:write"

# Columnas que se usan en el tablero: (título, tipo con el que se crea, tipos que se aceptan).
# Si no existen, se crean la primera vez. Monday reporta algunos tipos con nombres antiguos.
COLUMNS = {
    "responsable": ("Responsable", "people", {"people", "multiple-person"}),
    "fecha": ("Fecha límite", "date", {"date"}),
    "estado": ("Estado", "status", {"status", "color"}),
    "minuta": ("Minuta", "link", {"link"}),
    "notas": ("Notas de la minuta", "text", {"text"}),
}
ESTADO_INICIAL = "Pendiente"
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MondayError(RuntimeError):
    pass


class MondayAuthError(MondayError):
    """El token de la persona ya no sirve (desinstaló la app o le quitaron el acceso): hay que reconectar."""


def _gql(token: str, query: str, variables: dict | None = None) -> dict:
    try:
        res = httpx.post(
            API_URL,
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": token, "Content-Type": "application/json"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise MondayError(f"No se pudo conectar con Monday: {exc}") from exc
    if res.status_code == 401:
        raise MondayAuthError("Monday ya no acepta tu conexión. Vuelve a conectar Monday en tu perfil.")
    try:
        data = res.json()
    except ValueError:
        raise MondayError(f"Respuesta inesperada de Monday (HTTP {res.status_code})")
    errors = data.get("errors") or ([{"message": data["error_message"]}] if data.get("error_message") else [])
    if errors or res.status_code >= 400:
        detail = "; ".join(e.get("message", str(e)) for e in errors) or f"HTTP {res.status_code}"
        raise MondayError(f"Monday devolvió un error: {detail}")
    return data["data"]


# ---------------------------------------------------------------- conexión de cada persona

def authorize_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.monday_client_id,
        "redirect_uri": settings.monday_redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> str:
    """Cambia el código de autorización por el token de la persona."""
    settings = get_settings()
    try:
        res = httpx.post(
            TOKEN_URL,
            data={
                "client_id": settings.monday_client_id,
                "client_secret": settings.monday_client_secret,
                "code": code,
                "redirect_uri": settings.monday_redirect_uri,
            },
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise MondayError(f"No se pudo conectar con Monday: {exc}") from exc
    try:
        token = res.json().get("access_token") if res.status_code == 200 else None
    except ValueError:
        token = None
    if not token:
        raise MondayError("Monday rechazó la autorización")
    return token


def me(token: str) -> dict:
    """{"name", "email"} de la cuenta de Monday dueña del token."""
    return _gql(token, "query { me { name email } }")["me"]


@dataclass
class BoardInfo:
    id: str
    name: str
    workspace: str
    url: str | None


def _board_info(b: dict) -> BoardInfo:
    return BoardInfo(str(b["id"]), b["name"], (b.get("workspace") or {}).get("name") or "Principal", b.get("url"))


def list_boards(token: str) -> list[BoardInfo]:
    """Tableros activos a los que la persona tiene acceso (sin subelementos ni documentos)."""
    boards: list[BoardInfo] = []
    for page in range(1, 11):  # hasta 1000 tableros
        data = _gql(
            token,
            "query ($page: Int!) { boards(limit: 100, page: $page, state: active) {"
            " id name type url workspace { name } } }",
            {"page": page},
        )["boards"]
        boards += [_board_info(b) for b in data if b.get("type") in (None, "board")]
        if len(data) < 100:
            break
    return sorted(boards, key=lambda b: (_norm(b.workspace), _norm(b.name)))


def board_info(token: str, board_id: str) -> BoardInfo:
    data = _gql(
        token, "query ($ids: [ID!]) { boards(ids: $ids) { id name type url workspace { name } } }", {"ids": [board_id]}
    )
    if not data["boards"] or data["boards"][0].get("type") not in (None, "board"):
        raise MondayError("No tienes acceso a ese tablero de Monday o ya no existe")
    return _board_info(data["boards"][0])


# ---------------------------------------------------------------- envío de acuerdos

def _norm(text: str) -> str:
    """Minúsculas y sin acentos, para comparar nombres."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9@._-]+", text.lower()))


def match_attendee(responsable: str | None, attendees: list[dict]) -> str | None:
    """Correo del asistente al que se refiere el responsable de la minuta (texto libre), si es inequívoco."""
    if not responsable:
        return None
    target = _norm(responsable)
    for a in attendees:
        if target == a["email"].lower():
            return a["email"]
    exact = [a for a in attendees if a.get("name") and _norm(a["name"]) == target]
    if len(exact) == 1:
        return exact[0]["email"]
    # "Juan" o "Juan Pérez" contra "Juan Pérez López": todas las palabras del responsable deben estar en el nombre
    words = set(target.split())
    partial = [
        a for a in attendees
        if words and words <= set(_norm(a.get("name") or a["email"].split("@")[0].replace(".", " ")).split())
    ]
    return partial[0]["email"] if len(partial) == 1 else None


@dataclass
class Board:
    name: str
    url: str | None
    columns: dict[str, str]  # clave de COLUMNS -> id de la columna en Monday


def _board(token: str, board_id: str) -> Board:
    data = _gql(token, "query ($ids: [ID!]) { boards(ids: $ids) { name url columns { id title type } } }", {"ids": [board_id]})
    if not data["boards"]:
        raise MondayError("No tienes acceso al tablero de Monday elegido o ya no existe")
    board = data["boards"][0]
    columns: dict[str, str] = {}
    for key, (title, create_type, types) in COLUMNS.items():
        same_type = [c for c in board["columns"] if c["type"] in types]
        found = next((c for c in same_type if _norm(c["title"]) == _norm(title)), None)
        if found is None and key in ("responsable", "fecha", "estado") and len(same_type) == 1:
            found = same_type[0]  # el tablero ya tiene una sola columna de ese tipo: se reutiliza aunque se llame distinto
        if found is None:
            created = _gql(
                token,
                "mutation ($board: ID!, $title: String!, $type: ColumnType!) {"
                " create_column(board_id: $board, title: $title, column_type: $type) { id } }",
                {"board": board_id, "title": title, "type": create_type},
            )
            columns[key] = created["create_column"]["id"]
        else:
            columns[key] = found["id"]
    return Board(board["name"], board.get("url"), columns)


def _monday_users(token: str, emails: list[str]) -> dict[str, dict]:
    """Usuarios de Monday por correo: {correo: {"id": int, "name": str}}."""
    if not emails:
        return {}
    data = _gql(token, "query ($emails: [String!]) { users(emails: $emails) { id name email } }", {"emails": emails})
    return {u["email"].lower(): {"id": int(u["id"]), "name": u.get("name") or ""} for u in data["users"] or []}


@dataclass
class ExportResult:
    board_id: str
    group_id: str
    item_ids: list[str]
    board_url: str | None
    notified: list[str] = field(default_factory=list)  # nombres de quienes recibieron aviso en Monday


def export_agreements(
    token: str, board_id: str, meeting_id: int, title: str, started_at, attendees: list[dict], minuta: Minuta,
    owner_name: str = "",
) -> ExportResult:
    """Crea en el tablero elegido un grupo con el título y fecha de la reunión, y un elemento por acuerdo.
    Todo se hace con el token de la persona: en Monday aparece como creado por ella."""
    settings = get_settings()
    if not minuta.acuerdos:
        raise MondayError("La minuta no tiene acuerdos")

    board = _board(token, board_id)
    me_id = str(_gql(token, "query { me { id } }")["me"]["id"])
    # Los asistentes del calendario suelen venir solo con correo: el nombre se completa con el directorio de Monday
    users = _monday_users(token, sorted({a["email"].lower() for a in attendees}))
    named = [{**a, "name": a.get("name") or users.get(a["email"].lower(), {}).get("name", "")} for a in attendees]
    responsables = {i: match_attendee(a.responsable, named) for i, a in enumerate(minuta.acuerdos)}

    group = _gql(
        token,
        "mutation ($board: ID!, $name: String!) { create_group(board_id: $board, group_name: $name) { id } }",
        {"board": board_id, "name": f"{title} · {started_at.astimezone():%d/%m/%Y}"[:255]},
    )["create_group"]["id"]

    link = f"{settings.frontend_url.rstrip('/')}/#/reuniones/{meeting_id}"
    item_ids: list[str] = []
    notified: list[str] = []
    for i, acuerdo in enumerate(minuta.acuerdos):
        cols = board.columns
        values: dict = {
            cols["estado"]: {"label": ESTADO_INICIAL},
            cols["minuta"]: {"url": link, "text": "Ver minuta"},
        }
        notas = []
        email = responsables[i]
        if email and email.lower() in users:
            values[cols["responsable"]] = {"personsAndTeams": [{"id": users[email.lower()]["id"], "kind": "person"}]}
        elif acuerdo.responsable:
            notas.append(f"Responsable: {acuerdo.responsable}" + (f" <{email}>" if email else ""))
        if acuerdo.fecha_limite and ISO_DATE.match(acuerdo.fecha_limite):
            values[cols["fecha"]] = {"date": acuerdo.fecha_limite}
        elif acuerdo.fecha_limite:
            notas.append(f"Fecha: {acuerdo.fecha_limite}")
        if notas:
            values[cols["notas"]] = " · ".join(notas)

        item = _gql(
            token,
            "mutation ($board: ID!, $group: String!, $name: String!, $values: JSON!) {"
            " create_item(board_id: $board, group_id: $group, item_name: $name, column_values: $values,"
            " create_labels_if_missing: true) { id } }",
            {
                "board": board_id,
                "group": group,
                "name": (acuerdo.descripcion.strip() or "Acuerdo sin descripción")[:255],
                "values": json.dumps(values, ensure_ascii=False),
            },
        )
        item_id = item["create_item"]["id"]
        item_ids.append(item_id)

        # Aviso en la campana de Monday (y su correo, si lo tiene activado) para quien quedó como responsable.
        # A quien sube los acuerdos no se le avisa: se lo asignó él mismo.
        person = users.get(email.lower()) if email else None
        if person and str(person["id"]) != me_id:
            texto = f"{owner_name} te asignó un acuerdo de la minuta \"{title}\": {acuerdo.descripcion.strip()}"
            try:
                _gql(
                    token,
                    "mutation ($user: ID!, $target: ID!, $text: String!) {"
                    " create_notification(user_id: $user, target_id: $target, text: $text, target_type: Project) { text } }",
                    {"user": str(person["id"]), "target": item_id, "text": texto[:900]},
                )
                if person["name"] not in notified:
                    notified.append(person["name"])
            except MondayAuthError:
                raise
            except MondayError as exc:  # el acuerdo ya se creó; sin aviso no se detiene el resto
                log.warning("No se pudo avisar en Monday al responsable del elemento %s: %s", item_id, exc)
    return ExportResult(board_id, group, item_ids, board.url, notified)
