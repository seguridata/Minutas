"""Gmail y Google Calendar con las credenciales del propio usuario."""

import base64
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ..auth import decrypt
from ..config import get_settings
from ..models import User
from .minutes import LOGO_CID, LOGO_PATH


def _credentials(user: User) -> Credentials:
    if not user.refresh_token_enc:
        raise RuntimeError("El usuario no ha autorizado el acceso a Google; debe volver a iniciar sesión")
    s = get_settings()
    return Credentials(
        token=None,
        refresh_token=decrypt(user.refresh_token_enc),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        # Sin scopes explícitos: el refresh conserva los que el usuario autorizó y no se
        # registran avisos porque Google devuelve email/profile con su nombre largo
    )


def send_email(user: User, to: list[str], cc: list[str], subject: str, html: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = f"{user.name} <{user.email}>" if user.name else user.email
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if f"cid:{LOGO_CID}" in html and LOGO_PATH.exists():
        # La parte HTML pasa a multipart/related con el logo como imagen en línea
        msg.get_payload()[1].add_related(LOGO_PATH.read_bytes(), "image", "png", cid=f"<{LOGO_CID}>", filename="logo-seguridata.png")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = build("gmail", "v1", credentials=_credentials(user), cache_discovery=False)
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _detect_platform(event: dict) -> str:
    if event.get("hangoutLink"):
        return "meet"
    blob = " ".join(
        [event.get("location", ""), event.get("description", "")]
        + [ep.get("uri", "") for ep in event.get("conferenceData", {}).get("entryPoints", [])]
    ).lower()
    if "zoom.us" in blob:
        return "zoom"
    if "teams.microsoft.com" in blob or "teams.live.com" in blob:
        return "teams"
    if "meet.google.com" in blob:
        return "meet"
    return "otra"


def _parse_time(value: dict) -> datetime:
    raw = value.get("dateTime") or value.get("date")
    dt = datetime.fromisoformat(re.sub(r"Z$", "+00:00", raw))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _calendar_tz(service) -> ZoneInfo | timezone:
    name = service.calendars().get(calendarId="primary").execute().get("timeZone", "")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def current_events(user: User) -> list[dict]:
    """Eventos del calendario principal en curso o que faltan hoy (según la zona horaria del calendario)."""
    now = datetime.now(timezone.utc)
    service = build("calendar", "v3", credentials=_credentials(user), cache_discovery=False)
    local_now = now.astimezone(_calendar_tz(service))
    end_of_day = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    items = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=(now - timedelta(hours=3)).isoformat(),
            # Cerca de la medianoche se incluye también lo que empieza en los siguientes 30 minutos
            timeMax=max(end_of_day, local_now + timedelta(minutes=30)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
        .get("items", [])
    )
    events = []
    for ev in items:
        if "dateTime" not in ev.get("start", {}):
            continue  # eventos de todo el día
        end = _parse_time(ev["end"])
        if end < now:
            continue
        events.append(
            {
                "id": ev["id"],
                "title": ev.get("summary", "(sin título)"),
                "start": _parse_time(ev["start"]),
                "end": end,
                "platform": _detect_platform(ev),
                "attendees": [
                    {"name": a.get("displayName", ""), "email": a["email"]}
                    for a in ev.get("attendees", [])
                    if a.get("email") and not a.get("resource") and a.get("responseStatus") != "declined"
                ],
            }
        )
    return events
