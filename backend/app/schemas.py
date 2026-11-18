from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---- Minuta (también es el esquema de salida estructurada que se pide a la IA) ----

class Tema(BaseModel):
    tema: str = Field(description="Nombre corto del tema tratado")
    detalle: str = Field(description="Qué se discutió sobre este tema, en 1 a 4 oraciones")


class Acuerdo(BaseModel):
    descripcion: str = Field(description="Acción concreta acordada")
    responsable: str | None = Field(description="Nombre de la persona responsable, o null si no se mencionó")
    fecha_limite: str | None = Field(
        description="Fecha límite en formato AAAA-MM-DD si se mencionó una fecha concreta, o el texto literal (p. ej. 'próxima semana'); null si no se mencionó"
    )


class Minuta(BaseModel):
    titulo: str
    resumen: str = Field(description="Resumen ejecutivo de la reunión en un párrafo")
    temas: list[Tema]
    decisiones: list[str] = Field(description="Decisiones tomadas durante la reunión")
    acuerdos: list[Acuerdo] = Field(description="Compromisos y tareas con responsable y fecha")
    pendientes: list[str] = Field(description="Preguntas abiertas o temas que quedaron sin resolver")


# ---- API ----

class Attendee(BaseModel):
    name: str = ""
    email: EmailStr


class Consent(BaseModel):
    aviso: str = Field(description="Texto del aviso que se compartió con los participantes")
    confirmado_en: datetime
    confirmado_por: str
    nadie_se_opuso: bool
    origen: str = Field("grabacion", pattern="^(grabacion|archivo)$")
    archivo: str | None = Field(None, description="Nombre del archivo de audio cuando la minuta sale de una grabación existente")


class MeetingCreate(BaseModel):
    title: str
    platform: str = "otra"
    started_at: datetime
    ended_at: datetime
    attendees: list[Attendee] = []
    transcript: str
    consent: Consent


class MeetingUpdate(BaseModel):
    title: str | None = None
    attendees: list[Attendee] | None = None
    minutes: Minuta | None = None


class MeetingSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    platform: str
    started_at: datetime
    status: str
    sent_at: datetime | None


class UsoIA(BaseModel):
    generaciones: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    costo_usd: float = 0.0
    sin_precio: int = Field(0, description="Generaciones cuyo modelo no está en la tabla de precios (no suman al costo)")


class UsoMensual(BaseModel):
    mes: str  # AAAA-MM
    propio: UsoIA


class MondaySolicitud(BaseModel):
    """Acuerdos que el dueño autorizó subir, por su posición en la minuta guardada."""

    acuerdos: list[int] = Field(min_length=1)
    # Tablero para esta minuta; si no se indica, se usa el tablero elegido en el perfil
    tablero_id: str | None = Field(None, max_length=50)


class MondayEnvio(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    item_ids: list[str]
    board_url: str | None
    notified: list[str] | None = None


class MeetingOut(MeetingSummary):
    ended_at: datetime
    attendees: list[Attendee]
    transcript: str
    consent: dict
    minutes: Minuta | None
    error: str | None
    uso: UsoIA = UsoIA()
    monday_exports: list[MondayEnvio] = []


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    name: str
    picture: str | None
    auto_send: bool
    cc_me: bool
    signature: str
    is_admin: bool = False
    monday_habilitado: bool = False  # la integración con Monday está encendida
    monday_oauth: bool = False  # hay app OAuth de Monday: se puede conectar con un botón además de pegar el token
    monday_conexion: "MondayConexion | None" = None  # la cuenta de Monday que conectó esta persona


class MondayConexion(BaseModel):
    cuenta: str
    tablero_id: str | None
    tablero: str | None
    tablero_url: str | None


class MondayToken(BaseModel):
    token: str = Field(min_length=20, max_length=2000)


class MondayTablero(BaseModel):
    id: str
    nombre: str
    espacio: str
    url: str | None


class MondayTableroElegir(BaseModel):
    id: str = Field(min_length=1, max_length=50)


UserOut.model_rebuild()


class UserUpdate(BaseModel):
    auto_send: bool | None = None
    cc_me: bool | None = None
    signature: str | None = None


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    platform: str
    attendees: list[Attendee]


# ---- Administración (solo metadatos: nunca transcripciones ni minutas de otras personas) ----

class AdminDia(BaseModel):
    dia: str  # AAAA-MM-DD (UTC)
    generaciones: int
    costo_usd: float


class AdminModelo(BaseModel):
    model: str
    uso: UsoIA


class AdminAlerta(BaseModel):
    meeting_id: int
    title: str
    owner_email: str
    status: str  # error | procesando (atorada)
    error: str | None
    created_at: datetime


class AdminResumen(BaseModel):
    mes: str
    costo: UsoIA
    costo_mes_anterior: float
    por_dia: list[AdminDia]
    por_modelo: list[AdminModelo]
    usuarios_registrados: int
    usuarios_activos: int = Field(description="Personas que generaron al menos una minuta en el mes")
    reuniones_por_estado: dict[str, int]
    reuniones_por_plataforma: dict[str, int]
    horas_grabadas: float
    costo_por_hora: float | None
    alertas: list[AdminAlerta]


class AdminCostoUsuario(BaseModel):
    email: str
    name: str
    por_mes: list[float]  # mismo orden que AdminCostos.meses
    total: float


class AdminCostos(BaseModel):
    meses: list[str]  # AAAA-MM, del más antiguo al más reciente
    total_por_mes: list[float]
    usuarios: list[AdminCostoUsuario]


class AdminUsuario(BaseModel):
    email: str
    name: str
    alta: datetime
    ultima_actividad: datetime | None
    google_conectado: bool
    envio_automatico: bool
    reuniones_total: int
    reuniones_mes: int
    enviadas_mes: int
    errores_mes: int
    horas_mes: float
    uso_mes: UsoIA
