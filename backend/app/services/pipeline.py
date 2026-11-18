"""Tareas en segundo plano: generar la minuta y enviarla."""

import logging

from ..db import SessionLocal
from ..models import Meeting, MondayExport, UsageRecord, utcnow
from ..schemas import Minuta
from . import google_api, monday
from .minutes import GenerationError, Usage, generate_minutes, render_email

log = logging.getLogger(__name__)


def send_meeting(meeting: Meeting) -> None:
    """Envía la minuta a los asistentes (y al dueño en copia según su perfil)."""
    owner = meeting.owner
    minuta = Minuta.model_validate(meeting.minutes)
    to = [a["email"] for a in meeting.attendees if a["email"].lower() != owner.email.lower()]
    cc = [owner.email] if owner.cc_me else []
    if not to:  # reunión sin otros asistentes registrados: se envía solo al dueño
        to, cc = [owner.email], []
    subject, html, text = render_email(minuta, meeting.started_at, meeting.platform, meeting.attendees, owner.signature)
    google_api.send_email(owner, to, cc, subject, html, text)
    meeting.status = "enviada"
    meeting.sent_at = utcnow()


def export_to_monday(meeting: Meeting, token: str, board_id: str, indices: list[int] | None = None) -> None:
    """Crea en el tablero de Monday del dueño los acuerdos que autorizó (por posición; None = todos)."""
    minuta = Minuta.model_validate(meeting.minutes)
    if indices is not None:
        minuta.acuerdos = [minuta.acuerdos[i] for i in sorted(set(indices))]
    owner = meeting.owner
    # El dueño también puede ser responsable aunque no esté en la lista de asistentes, y su nombre se conoce por Google
    people = [
        {**a, "name": a.get("name") or (owner.name if a["email"].lower() == owner.email.lower() else "")}
        for a in meeting.attendees
    ]
    if all(a["email"].lower() != owner.email.lower() for a in people):
        people.append({"name": owner.name, "email": owner.email})
    result = monday.export_agreements(
        token, board_id, meeting.id, meeting.title, meeting.started_at, people, minuta, owner_name=owner.name or owner.email
    )
    meeting.monday_exports.append(
        MondayExport(
            board_id=result.board_id, group_id=result.group_id, item_ids=result.item_ids, board_url=result.board_url,
            notified=result.notified,
        )
    )


def _record_usage(db, meeting: Meeting, usage: Usage) -> None:
    db.add(
        UsageRecord(
            user_id=meeting.owner_id,
            meeting_id=meeting.id,
            model=usage.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
        )
    )


def process_meeting(meeting_id: int) -> None:
    with SessionLocal() as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None:
            return
        try:
            try:
                minuta, usage = generate_minutes(
                    meeting.title, meeting.platform, meeting.started_at, meeting.attendees, meeting.transcript
                )
            except GenerationError as exc:
                _record_usage(db, meeting, exc.usage)  # se cobró aunque no haya minuta
                db.commit()
                raise
            _record_usage(db, meeting, usage)
            meeting.minutes = minuta.model_dump()
            meeting.status = "lista"
            meeting.error = None
            db.commit()
            if meeting.owner.auto_send:
                send_meeting(meeting)
                db.commit()
            # Monday nunca se llena solo: el dueño revisa y autoriza qué acuerdos se suben desde la web
        except Exception as exc:  # se guarda el error para mostrarlo en la web
            log.exception("Error procesando la reunión %s", meeting_id)
            db.rollback()
            meeting.status = "error" if meeting.minutes is None else "lista"
            meeting.error = str(exc)
            db.commit()
