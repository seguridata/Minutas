import json
from datetime import datetime, timezone
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .config import get_settings
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Fecha siempre en UTC. SQLite no guarda la zona horaria y devolvería fechas "ingenuas" que el
    navegador interpreta como hora local."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc) if value is not None else None

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


@lru_cache
def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key)


def is_encrypted(value: str) -> bool:
    return value.startswith("gAAAAA")  # prefijo de todo token Fernet (versión 0x80 en base64)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not is_encrypted(value):
        return value  # registro anterior al cifrado; se cifra al arrancar el backend
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise RuntimeError("No se pudo descifrar el contenido: FERNET_KEY no es la misma con la que se cifró") from None


class EncryptedText(TypeDecorator):
    """Texto cifrado en la base con FERNET_KEY (AES-128-CBC + HMAC-SHA256). Se descifra solo al leerlo."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt(value)


class EncryptedJSON(TypeDecorator):
    """Igual que EncryptedText, para estructuras JSON (la minuta)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(json.dumps(value, ensure_ascii=False))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        value = decrypt(value)
        return json.loads(value) if isinstance(value, str) else value


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    picture: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Refresh token de Google cifrado con Fernet; permite enviar correo y leer el calendario
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Preferencias del perfil
    auto_send: Mapped[bool] = mapped_column(Boolean, default=False)
    cc_me: Mapped[bool] = mapped_column(Boolean, default=True)
    signature: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    meetings: Mapped[list["Meeting"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    monday: Mapped["MondayConnection | None"] = relationship(cascade="all, delete-orphan", uselist=False)


class MondayConnection(Base):
    """Cuenta de Monday que conectó una persona y el tablero que eligió para sus acuerdos."""

    __tablename__ = "monday_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    # Token OAuth de Monday de la persona: solo ve y escribe en los tableros a los que ella tiene acceso
    token: Mapped[str] = mapped_column(EncryptedText())
    account_name: Mapped[str] = mapped_column(String(255), default="")  # nombre de la persona en Monday
    board_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    board_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    board_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    title: Mapped[str] = mapped_column(String(500))
    platform: Mapped[str] = mapped_column(String(20), default="otra")  # meet | teams | zoom | otra
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attendees: Mapped[list] = mapped_column(JSON, default=list)  # [{"name": str, "email": str}]
    # Contenido de la reunión: cifrado en la base, solo se descifra al leerlo
    transcript: Mapped[str] = mapped_column(EncryptedText())

    # Registro del consentimiento obtenido antes de grabar
    consent: Mapped[dict] = mapped_column(JSON)

    status: Mapped[str] = mapped_column(String(20), default="procesando")  # procesando | lista | enviada | error
    minutes: Mapped[dict | None] = mapped_column(EncryptedJSON(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    owner: Mapped[User] = relationship(back_populates="meetings")
    monday_exports: Mapped[list["MondayExport"]] = relationship(
        cascade="all, delete-orphan", order_by="MondayExport.created_at"
    )


class UsageRecord(Base):
    """Una llamada a Claude. Se conserva aunque se borre la reunión, para no perder el costo real."""

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    meeting_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)  # sin FK a propósito
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)  # null si el modelo no tiene precio registrado
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class MondayExport(Base):
    """Acuerdos de una reunión enviados a Monday; evita crearlos dos veces por accidente."""

    __tablename__ = "monday_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    board_id: Mapped[str] = mapped_column(String(50))
    group_id: Mapped[str] = mapped_column(String(100))
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    board_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    notified: Mapped[list] = mapped_column(JSON, default=list)  # responsables que recibieron aviso en Monday
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


def add_missing_columns(engine) -> None:
    """create_all no agrega columnas a tablas que ya existen; aquí van las que se sumaron después."""
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(engine).get_columns("monday_exports")}
    if "notified" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE monday_exports ADD COLUMN notified JSON"))


def encrypt_legacy_rows(engine) -> int:
    """Cifra las transcripciones y minutas guardadas antes de que existiera el cifrado. Devuelve cuántas cambió."""
    from sqlalchemy import text

    changed = 0
    with engine.begin() as conn:
        for id_, transcript, minutes in conn.execute(text("SELECT id, transcript, minutes FROM meetings")).all():
            updates = {}
            if transcript is not None and not is_encrypted(transcript):
                updates["transcript"] = encrypt(transcript)
            if minutes is not None:
                raw = minutes if isinstance(minutes, str) else json.dumps(minutes, ensure_ascii=False)
                if not is_encrypted(raw):
                    updates["minutes"] = encrypt(raw)
            if updates:
                sets = ", ".join(f"{k} = :{k}" for k in updates)
                conn.execute(text(f"UPDATE meetings SET {sets} WHERE id = :id"), {**updates, "id": id_})
                changed += 1
    return changed
