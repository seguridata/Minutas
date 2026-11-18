"""Panel de administración: costos, usuarios y monitoreo.

Solo para los correos en ADMIN_EMAILS. Devuelve metadatos (quién, cuánto, en qué estado); nunca el
contenido de las reuniones de otras personas.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import current_user
from .config import get_settings
from .db import get_db
from .models import Meeting, UsageRecord, User
from .routes import _USAGE_COLUMNS, _uso, _usage_totals, month_bounds
from .schemas import AdminAlerta, AdminCostoUsuario, AdminCostos, AdminDia, AdminModelo, AdminResumen, AdminUsuario, UsoIA

STUCK_AFTER = timedelta(minutes=15)  # una minuta "procesando" más tiempo que esto se reporta como atorada
MES = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="AAAA-MM; por defecto el mes actual")


def require_admin(user: User = Depends(current_user)) -> User:
    if not get_settings().is_admin(user.email):
        raise HTTPException(403, "Solo para administradores")
    return user


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _hours(meeting: Meeting) -> float:
    return max((meeting.ended_at - meeting.started_at).total_seconds(), 0) / 3600


@router.get("/resumen", response_model=AdminResumen)
def resumen(mes: str | None = MES, db: Session = Depends(get_db)):
    start, end = month_bounds(mes)
    period = (UsageRecord.created_at >= start, UsageRecord.created_at < end)
    prev_start, _ = month_bounds(f"{start - timedelta(days=1):%Y-%m}")

    costo = _usage_totals(db, *period)
    anterior = _usage_totals(db, UsageRecord.created_at >= prev_start, UsageRecord.created_at < start)

    dia = func.date(UsageRecord.created_at)
    por_dia = [
        AdminDia(dia=str(d), generaciones=n, costo_usd=round(c, 4))
        for d, n, c in db.execute(
            select(dia, func.count(UsageRecord.id), func.coalesce(func.sum(UsageRecord.cost_usd), 0.0))
            .where(*period).group_by(dia).order_by(dia)
        )
    ]
    por_modelo = [
        AdminModelo(model=r[0], uso=_uso(r[1:]))
        for r in db.execute(select(UsageRecord.model, *_USAGE_COLUMNS).where(*period).group_by(UsageRecord.model))
    ]

    meetings = db.scalars(select(Meeting).where(Meeting.created_at >= start, Meeting.created_at < end)).all()
    horas = sum(_hours(m) for m in meetings)

    # Errores del mes y minutas atoradas en "procesando" (de cualquier fecha: siguen sin resolverse)
    stuck_before = datetime.now(timezone.utc) - STUCK_AFTER
    alertas = db.execute(
        select(Meeting, User.email)
        .join(User, Meeting.owner_id == User.id)
        .where(
            ((Meeting.status == "error") & (Meeting.created_at >= start) & (Meeting.created_at < end))
            | ((Meeting.status == "procesando") & (Meeting.created_at < stuck_before))
        )
        .order_by(Meeting.created_at.desc())
        .limit(50)
    ).all()

    return AdminResumen(
        mes=f"{start:%Y-%m}",
        costo=costo,
        costo_mes_anterior=anterior.costo_usd,
        por_dia=por_dia,
        por_modelo=por_modelo,
        usuarios_registrados=db.scalar(select(func.count(User.id))),
        usuarios_activos=db.scalar(select(func.count(func.distinct(UsageRecord.user_id))).where(*period)),
        reuniones_por_estado=dict(Counter(m.status for m in meetings)),
        reuniones_por_plataforma=dict(Counter(m.platform for m in meetings)),
        horas_grabadas=round(horas, 2),
        costo_por_hora=round(costo.costo_usd / horas, 4) if horas else None,
        alertas=[
            AdminAlerta(
                meeting_id=m.id, title=m.title, owner_email=email, status=m.status, error=m.error, created_at=m.created_at
            )
            for m, email in alertas
        ],
    )


@router.get("/usuarios", response_model=list[AdminUsuario])
def usuarios(mes: str | None = MES, db: Session = Depends(get_db)):
    start, end = month_bounds(mes)
    uso = {
        r[0]: _uso(r[1:])
        for r in db.execute(
            select(UsageRecord.user_id, *_USAGE_COLUMNS)
            .where(UsageRecord.created_at >= start, UsageRecord.created_at < end)
            .group_by(UsageRecord.user_id)
        )
    }
    ultima = dict(db.execute(select(Meeting.owner_id, func.max(Meeting.created_at)).group_by(Meeting.owner_id)).all())
    total = dict(db.execute(select(Meeting.owner_id, func.count(Meeting.id)).group_by(Meeting.owner_id)).all())
    del_mes: dict[int, list[Meeting]] = {}
    for m in db.scalars(select(Meeting).where(Meeting.created_at >= start, Meeting.created_at < end)):
        del_mes.setdefault(m.owner_id, []).append(m)

    out = []
    for u in db.scalars(select(User)):
        ms = del_mes.get(u.id, [])
        out.append(
            AdminUsuario(
                email=u.email,
                name=u.name,
                alta=u.created_at,
                ultima_actividad=ultima.get(u.id),
                google_conectado=u.refresh_token_enc is not None,
                envio_automatico=u.auto_send,
                reuniones_total=total.get(u.id, 0),
                reuniones_mes=len(ms),
                enviadas_mes=sum(m.status == "enviada" for m in ms),
                errores_mes=sum(m.status == "error" for m in ms),
                horas_mes=round(sum(_hours(m) for m in ms), 2),
                uso_mes=uso.get(u.id, UsoIA()),
            )
        )
    out.sort(key=lambda a: (-a.uso_mes.costo_usd, -a.reuniones_mes, a.email))
    return out


@router.get("/costos", response_model=AdminCostos)
def costos(
    meses: int = Query(6, ge=1, le=24, description="Cuántos meses hacia atrás, incluido el actual"),
    db: Session = Depends(get_db),
):
    """Costo de IA de cada persona mes por mes, para ver cómo va creciendo su uso."""
    start, _ = month_bounds(None)
    keys = [f"{start:%Y-%m}"]
    for _ in range(meses - 1):
        start, _ = month_bounds(f"{start - timedelta(days=1):%Y-%m}")
        keys.insert(0, f"{start:%Y-%m}")

    # Se agrupa en Python para no depender de funciones de fecha propias de SQLite o Postgres
    por_usuario: dict[int, dict[str, float]] = {}
    for user_id, created_at, c in db.execute(
        select(UsageRecord.user_id, UsageRecord.created_at, UsageRecord.cost_usd).where(UsageRecord.created_at >= start)
    ):
        meses_u = por_usuario.setdefault(user_id, {})
        k = f"{created_at.astimezone(timezone.utc):%Y-%m}"
        meses_u[k] = meses_u.get(k, 0.0) + (c or 0.0)

    usuarios = []
    for u in db.scalars(select(User)):
        valores = [round(por_usuario.get(u.id, {}).get(k, 0.0), 4) for k in keys]
        usuarios.append(AdminCostoUsuario(email=u.email, name=u.name, por_mes=valores, total=round(sum(valores), 4)))
    usuarios.sort(key=lambda x: (-x.total, x.email))
    return AdminCostos(
        meses=keys,
        total_por_mes=[round(sum(u.por_mes[i] for u in usuarios), 4) for i in range(len(keys))],
        usuarios=usuarios,
    )
