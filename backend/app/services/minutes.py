"""Generación de la minuta con IA (Claude u OpenAI) y armado del correo."""

import html
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import anthropic
import openai
import pydantic

from ..config import get_settings
from ..schemas import Minuta

PLATFORMS = {"meet": "Google Meet", "teams": "Microsoft Teams", "zoom": "Zoom", "otra": "Otra"}

SYSTEM_PROMPT = """Eres el asistente que redacta las minutas de reuniones internas de la empresa.
Recibes la transcripción automática de una reunión (puede tener errores de reconocimiento de voz,
palabras cortadas y no indica quién habla). Redacta una minuta fiel, clara y en español profesional.

- Basa todo exclusivamente en la transcripción; no inventes acuerdos, responsables ni fechas.
- Si un responsable o una fecha no se dice explícitamente, déjalo en null.
- Usa los nombres de los asistentes para atribuir responsables cuando la transcripción lo permita.
- Convierte fechas relativas ("el viernes", "en dos semanas") a AAAA-MM-DD usando la fecha de la reunión; si es ambigua, conserva el texto literal.
- Corrige errores evidentes de transcripción en nombres propios y términos técnicos cuando el contexto lo deje claro.
- Omite charla informal que no aporte a la reunión.
- Si la transcripción está vacía o no contiene una reunión de trabajo, indícalo en el resumen y deja las listas vacías."""

# Precios de lista en USD por millón de tokens (entrada, salida). Actualizar si el proveedor cambia sus precios.
# Los de OpenAI son los publicados a noviembre de 2026; confírmalos en openai.com/api/pricing.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "gpt-5.5": (5.00, 30.00),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5": (1.25, 10.00),
}
# Algunos modelos cobran más cuando la entrada pasa de cierto tamaño: prefijo -> (umbral, precios)
LONG_PROMPT = {
    "claude-haiku-5-5": (100_000, (0.50, 2.50)),
    "gpt-5.5": (272_000, (10.00, 45.00)),
}


def _lookup(table: dict, model: str):
    # El prefijo más largo gana, para que "gpt-5" no se lleve a "gpt-5.5" o "gpt-5-mini"
    matches = [name for name in table if model.startswith(name)]
    return table[max(matches, key=len)] if matches else None


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Costo en USD de una llamada; None si el modelo no está en la tabla de precios."""
    prices = _lookup(PRICES, model)
    if prices is None:
        return None
    long = _lookup(LONG_PROMPT, model)
    if long and input_tokens > long[0]:
        prices = long[1]
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


@dataclass
class Usage:
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def cost_usd(self) -> float | None:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


class GenerationError(RuntimeError):
    """La llamada se cobró pero no produjo una minuta utilizable."""

    def __init__(self, message: str, usage: Usage):
        super().__init__(message)
        self.usage = usage


@lru_cache
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key or None)


@lru_cache
def _openai_client() -> openai.OpenAI:
    return openai.OpenAI(api_key=get_settings().openai_api_key or None)


def generate_minutes(
    title: str, platform: str, started_at: datetime, attendees: list[dict], transcript: str
) -> tuple[Minuta, Usage]:
    asistentes = "\n".join(f"- {a.get('name') or a['email']} <{a['email']}>" for a in attendees) or "- (no registrados)"
    user_msg = (
        f"Título: {title}\n"
        f"Plataforma: {PLATFORMS.get(platform, platform)}\n"
        f"Fecha de la reunión: {started_at.astimezone():%Y-%m-%d (%A) %H:%M}\n"
        f"Asistentes:\n{asistentes}\n\n"
        f"<transcripcion>\n{transcript}\n</transcripcion>"
    )
    if get_settings().provider == "openai":
        return _generate_openai(user_msg)
    return _generate_anthropic(user_msg)


def _generate_anthropic(user_msg: str) -> tuple[Minuta, Usage]:
    response = _client().beta.messages.parse(
        model=get_settings().claude_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        output_config={"effort": "medium"},
        output_format=Minuta,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    # response.model es el modelo que realmente respondió (puede diferir si hubo respaldo del servidor)
    usage = Usage(response.model, response.usage.input_tokens, response.usage.output_tokens)
    if response.stop_reason == "refusal":
        raise GenerationError("El modelo no pudo generar la minuta para esta transcripción", usage)
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise GenerationError("La respuesta del modelo quedó incompleta; intenta regenerar la minuta", usage)
    return response.parsed_output, usage


def _generate_openai(user_msg: str) -> tuple[Minuta, Usage]:
    try:
        raw = _openai_client().responses.with_raw_response.parse(
            model=get_settings().openai_model,
            instructions=SYSTEM_PROMPT,
            input=[{"role": "user", "content": user_msg}],
            text_format=Minuta,
            max_output_tokens=16000,
            reasoning={"effort": "medium"},
            store=False,  # que OpenAI no guarde la transcripción ni la minuta en su historial de respuestas
        )
    except openai.AuthenticationError:
        raise RuntimeError("OpenAI rechazó la llave (OPENAI_API_KEY). Revísala en el .env del backend.") from None
    except openai.RateLimitError as exc:
        if "credit" in str(exc).lower() or "quota" in str(exc).lower():
            raise RuntimeError(
                "La cuenta de OpenAI no tiene saldo. Agrega crédito en platform.openai.com > Billing y regenera la minuta."
            ) from None
        raise RuntimeError("OpenAI está limitando las solicitudes; espera un momento y regenera la minuta.") from None
    except openai.NotFoundError:
        raise RuntimeError(f"OpenAI no reconoce el modelo {get_settings().openai_model!r} (OPENAI_MODEL).") from None
    try:
        response = raw.parse()
    except pydantic.ValidationError:
        # JSON cortado (se acabaron los tokens): la llamada igual se cobró, así que se registra su uso
        data = raw.http_response.json()
        u = data.get("usage") or {}
        usage = Usage(data.get("model", get_settings().openai_model), u.get("input_tokens", 0), u.get("output_tokens", 0))
        raise GenerationError("La respuesta del modelo quedó incompleta; intenta regenerar la minuta", usage) from None
    u = response.usage
    usage = Usage(response.model, u.input_tokens if u else 0, u.output_tokens if u else 0)
    refused = any(
        getattr(c, "type", None) == "refusal"
        for item in response.output if item.type == "message"
        for c in item.content
    )
    if refused:
        raise GenerationError("El modelo no pudo generar la minuta para esta transcripción", usage)
    if response.status == "incomplete" or response.output_parsed is None:
        raise GenerationError("La respuesta del modelo quedó incompleta; intenta regenerar la minuta", usage)
    return response.output_parsed, usage


# ---------------------------------------------------------------- correo

# Colores de SeguriData; el verde de marca solo se usa en acentos porque el texto sobre él no se lee bien
GREEN, DARK_GREEN, SOFT_GREEN = "#81bb26", "#4f7a12", "#eef6e1"
GRAY, TEXT, BORDER = "#5d676f", "#2b3036", "#dfe3dc"

# El logo va incrustado en el correo (Content-ID) para que se vea sin depender de un servidor público
LOGO_CID = "logo-seguridata"
LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "logo-seguridata.png"


def _e(value: str | None) -> str:
    return html.escape(value or "")


def render_email(minuta: Minuta, started_at: datetime, platform: str, attendees: list[dict], signature: str) -> tuple[str, str, str]:
    """Devuelve (asunto, html, texto plano)."""
    fecha = started_at.astimezone().strftime("%d/%m/%Y %H:%M")
    subject = f"Minuta: {minuta.titulo} ({started_at.astimezone():%d/%m/%Y})"
    nombres = ", ".join(a.get("name") or a["email"] for a in attendees) or "—"

    cell = f"padding:8px 10px;border-bottom:1px solid {BORDER};vertical-align:top"
    acuerdos_rows = "".join(
        f"<tr><td style='{cell}'>{_e(a.descripcion)}</td>"
        f"<td style='{cell};white-space:nowrap'>{_e(a.responsable) or '—'}</td>"
        f"<td style='{cell};white-space:nowrap'>{_e(a.fecha_limite) or '—'}</td></tr>"
        for a in minuta.acuerdos
    )
    th = f"padding:8px 10px;text-align:left;font-size:12px;color:{DARK_GREEN};background:{SOFT_GREEN};border-bottom:2px solid {GREEN}"
    acuerdos_html = (
        f"<table role='presentation' cellpadding='0' cellspacing='0' style='border-collapse:collapse;width:100%;font-size:14px'>"
        f"<tr><th style='{th}'>Acuerdo</th><th style='{th}'>Responsable</th><th style='{th}'>Fecha límite</th></tr>"
        f"{acuerdos_rows}</table>"
        if minuta.acuerdos
        else "<p style='margin:0'>No se registraron acuerdos.</p>"
    )

    def ul(items: list[str]) -> str:
        if not items:
            return "<p style='margin:0'>—</p>"
        return "<ul style='margin:0;padding-left:20px'>" + "".join(f"<li style='margin:0 0 4px'>{_e(i)}</li>" for i in items) + "</ul>"

    def section(title: str, content: str) -> str:
        return (
            f"<h3 style='margin:24px 0 8px;font-size:13px;letter-spacing:.5px;text-transform:uppercase;color:{DARK_GREEN}'>"
            f"{title}</h3>{content}"
        )

    temas_html = "".join(f"<p style='margin:0 0 8px'><b>{_e(t.tema)}.</b> {_e(t.detalle)}</p>" for t in minuta.temas) or "<p style='margin:0'>—</p>"
    firma_html = f"<p style='margin:24px 0 0'>{_e(signature).replace(chr(10), '<br>')}</p>" if signature else ""

    # Tablas y estilos en línea para que se vea igual en Gmail y Outlook
    body_html = f"""<div style="background:#f4f6f2;padding:24px 12px;font-family:'Segoe UI',Arial,sans-serif">
<table role="presentation" cellpadding="0" cellspacing="0" align="center" style="width:100%;max-width:680px;background:#ffffff;border:1px solid {BORDER};border-top:4px solid {GREEN};border-radius:8px;border-collapse:separate">
<tr><td style="padding:20px 28px 16px;border-bottom:1px solid {BORDER}">
<img src="cid:{LOGO_CID}" width="170" alt="SeguriData" style="display:block;border:0;width:170px;height:auto">
</td></tr>
<tr><td style="padding:20px 28px 28px;font-size:14px;line-height:1.5;color:{TEXT}">
<p style="margin:0 0 4px;font-size:12px;letter-spacing:.5px;text-transform:uppercase;color:{GRAY}">Minuta de reunión</p>
<h2 style="margin:0 0 6px;font-size:21px;color:{TEXT}">{_e(minuta.titulo)}</h2>
<p style="margin:0;color:{GRAY};font-size:13px">{fecha} · {PLATFORMS.get(platform, platform)}<br>Asistentes: {_e(nombres)}</p>
{section("Resumen", f"<p style='margin:0'>{_e(minuta.resumen)}</p>")}
{section("Acuerdos", acuerdos_html)}
{section("Decisiones", ul(minuta.decisiones))}
{section("Temas tratados", temas_html)}
{section("Pendientes", ul(minuta.pendientes))}
{firma_html}
</td></tr>
<tr><td style="padding:14px 28px;background:#f7f9f5;border-top:1px solid {BORDER};border-radius:0 0 8px 8px;color:{GRAY};font-size:12px;line-height:1.5">
Minuta generada con IA a partir de la transcripción de la reunión, grabada con aviso previo a los participantes.
Revisa los acuerdos y responde a este correo si algo no es correcto.<br>
<span style="color:{GREEN};font-weight:bold">SeguriData</span> · Information Security
</td></tr>
</table>
</div>"""

    lines = [minuta.titulo, f"{fecha} · {PLATFORMS.get(platform, platform)}", f"Asistentes: {nombres}", "",
             "RESUMEN", minuta.resumen, "", "ACUERDOS"]
    lines += [f"- {a.descripcion} | Responsable: {a.responsable or '—'} | Fecha: {a.fecha_limite or '—'}" for a in minuta.acuerdos] or ["—"]
    lines += ["", "DECISIONES"] + ([f"- {d}" for d in minuta.decisiones] or ["—"])
    lines += ["", "TEMAS TRATADOS"] + ([f"- {t.tema}: {t.detalle}" for t in minuta.temas] or ["—"])
    lines += ["", "PENDIENTES"] + ([f"- {p}" for p in minuta.pendientes] or ["—"])
    if signature:
        lines += ["", signature]
    lines += ["", "Minuta generada con IA a partir de la transcripción de la reunión, grabada con aviso previo a los participantes."]
    return subject, body_html, "\n".join(lines)
