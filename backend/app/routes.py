from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import current_user
from .db import get_db
from .config import get_settings
from .models import Meeting, UsageRecord, User
from .schemas import (
    CalendarEvent,
    MeetingCreate,
    MeetingOut,
    MeetingSummary,
    MeetingUpdate,
    MondayConexion,
    MondaySolicitud,
    UserOut,
    UserUpdate,
    UsoIA,
    UsoMensual,
)
from .services import google_api
from .services.pipeline import export_to_monday, process_meeting, send_meeting
from .monday_routes import connection as monday_connection, monday_call

router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------- perfil

def _user_out(user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.is_admin = get_settings().is_admin(user.email)
    out.monday_habilitado = get_settings().monday_enabled
    out.monday_oauth = get_settings().monday_oauth
    if out.monday_habilitado and user.monday is not None:
        c = user.monday
        out.monday_conexion = MondayConexion(
            cuenta=c.account_name, tablero_id=c.board_id, tablero=c.board_name, tablero_url=c.board_url
        )
    return out


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return _user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(body: UserUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    db.commit()
    return _user_out(user)


@router.get("/calendar/current", response_model=list[CalendarEvent])
def calendar_current(user: User = Depends(current_user)):
    try:
        return google_api.current_events(user)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo leer el calendario: {exc}")


# ---------------------------------------------------------------- consumo de IA

_USAGE_COLUMNS = (
    func.count(UsageRecord.id),
    func.coalesce(func.sum(UsageRecord.input_tokens), 0),
    func.coalesce(func.sum(UsageRecord.output_tokens), 0),
    func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
    func.count(UsageRecord.cost_usd),  # COUNT ignora los NULL: generaciones con precio conocido
)


def _uso(row) -> UsoIA:
    total, inp, out, cost, priced = row
    return UsoIA(generaciones=total, input_tokens=inp, output_tokens=out, costo_usd=round(cost, 4), sin_precio=total - priced)


def _usage_totals(db: Session, *where) -> UsoIA:
    return _uso(db.execute(select(*_USAGE_COLUMNS).where(*where)).one())


def month_bounds(mes: str | None) -> tuple[datetime, datetime]:
    """Inicio y fin del mes AAAA-MM (por defecto el actual). Se corta en UTC, igual que se guardan las fechas."""
    start = datetime.strptime(mes, "%Y-%m").replace(tzinfo=timezone.utc) if mes else datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, end


def _meeting_out(meeting: Meeting, db: Session) -> MeetingOut:
    out = MeetingOut.model_validate(meeting)
    out.uso = _usage_totals(db, UsageRecord.meeting_id == meeting.id, UsageRecord.user_id == meeting.owner_id)
    return out


@router.get("/usage", response_model=UsoMensual)
def usage(
    mes: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="AAAA-MM; por defecto el mes actual"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = month_bounds(mes)
    # Solo el consumo propio; el de los demás está en el panel de administración (/api/admin)
    return UsoMensual(
        mes=f"{start:%Y-%m}",
        propio=_usage_totals(db, UsageRecord.user_id == user.id, UsageRecord.created_at >= start, UsageRecord.created_at < end),
    )


# ---------------------------------------------------------------- reuniones

def _own_meeting(meeting_id: int, user: User, db: Session) -> Meeting:
    meeting = db.get(Meeting, meeting_id)
    # Cada usuario solo ve sus propias reuniones
    if meeting is None or meeting.owner_id != user.id:
        raise HTTPException(404, "Reunión no encontrada")
    return meeting


def _process(background: BackgroundTasks, meeting: Meeting, db: Session) -> None:
    """Genera la minuta después de responder; en Vercel, dentro de la petición (allá no hay segundo plano)."""
    if get_settings().vercel:
        process_meeting(meeting.id)
        db.refresh(meeting)
    else:
        background.add_task(process_meeting, meeting.id)


@router.post("/meetings", response_model=MeetingSummary, status_code=201)
def create_meeting(
    body: MeetingCreate,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not body.consent.nadie_se_opuso:
        raise HTTPException(400, "No se puede generar la minuta sin el consentimiento de los participantes")
    if not body.transcript.strip():
        raise HTTPException(400, "La transcripción está vacía")
    meeting = Meeting(
        owner_id=user.id,
        title=body.title,
        platform=body.platform,
        started_at=body.started_at,
        ended_at=body.ended_at,
        attendees=[a.model_dump() for a in body.attendees],
        transcript=body.transcript,
        consent=body.consent.model_dump(mode="json"),
        status="procesando",
    )
    db.add(meeting)
    db.commit()
    _process(background, meeting, db)
    return meeting


@router.get("/meetings", response_model=list[MeetingSummary])
def list_meetings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(Meeting).where(Meeting.owner_id == user.id).order_by(Meeting.started_at.desc())
    ).all()


@router.get("/meetings/{meeting_id}", response_model=MeetingOut)
def get_meeting(meeting_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _meeting_out(_own_meeting(meeting_id, user, db), db)


@router.patch("/meetings/{meeting_id}", response_model=MeetingOut)
def update_meeting(
    meeting_id: int, body: MeetingUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    meeting = _own_meeting(meeting_id, user, db)
    if body.title is not None:
        meeting.title = body.title
    if body.attendees is not None:
        meeting.attendees = [a.model_dump() for a in body.attendees]
    if body.minutes is not None:
        meeting.minutes = body.minutes.model_dump()
    db.commit()
    return _meeting_out(meeting, db)


@router.post("/meetings/{meeting_id}/regenerate", response_model=MeetingSummary)
def regenerate(
    meeting_id: int,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    meeting = _own_meeting(meeting_id, user, db)
    meeting.status = "procesando"
    meeting.minutes = None
    meeting.error = None
    db.commit()
    _process(background, meeting, db)
    return meeting


@router.post("/meetings/{meeting_id}/send", response_model=MeetingOut)
def send(meeting_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    meeting = _own_meeting(meeting_id, user, db)
    if meeting.minutes is None:
        raise HTTPException(409, "La minuta todavía no está lista")
    try:
        send_meeting(meeting)
    except Exception as exc:
        raise HTTPException(502, f"No se pudo enviar el correo: {exc}")
    meeting.error = None
    db.commit()
    return _meeting_out(meeting, db)


@router.post("/meetings/{meeting_id}/monday", response_model=MeetingOut)
def send_to_monday(
    meeting_id: int,
    body: MondaySolicitud,
    de_nuevo: bool = Query(False, description="Crear los acuerdos otra vez aunque ya se hayan enviado"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    meeting = _own_meeting(meeting_id, user, db)
    conn = monday_connection(user)
    board_id = body.tablero_id or conn.board_id
    if not board_id:
        raise HTTPException(409, "Elige en tu perfil el tablero de Monday donde van tus acuerdos")
    if meeting.minutes is None:
        raise HTTPException(409, "La minuta todavía no está lista")
    total = len(meeting.minutes.get("acuerdos") or [])
    if not total:
        raise HTTPException(409, "La minuta no tiene acuerdos que enviar")
    if any(i < 0 or i >= total for i in body.acuerdos):
        raise HTTPException(422, "Los acuerdos elegidos no coinciden con la minuta guardada; recarga la página")
    if meeting.monday_exports and not de_nuevo:
        raise HTTPException(409, "Los acuerdos ya se enviaron a Monday")
    monday_call(export_to_monday, meeting, conn.token, board_id, body.acuerdos, db=db, user=user)
    meeting.error = None
    db.commit()
    return _meeting_out(meeting, db)


@router.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_own_meeting(meeting_id, user, db))
    db.commit()
