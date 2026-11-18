"""Ventana de la app de escritorio (CustomTkinter con los colores de SeguriData)."""

import queue
import re
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from . import api as api_mod
from .config import BUNDLE, WEB_URL, consent_notice

PLATFORMS = {"Google Meet": "meet", "Microsoft Teams": "teams", "Zoom": "zoom", "Otra": "otra"}
PLATFORM_LABEL = {v: k for k, v in PLATFORMS.items()}
_EMAIL_RE = re.compile(r"^\s*(?:(.*?)\s*<\s*)?([^<>\s@]+@[^<>\s@]+\.[^<>\s@]+)\s*>?\s*$")
NO_EVENT = "(Reunión sin evento en calendario)"
AUDIO_TYPES = [
    ("Audio o video", "*.mp3 *.m4a *.wav *.ogg *.opus *.flac *.aac *.wma *.webm *.mp4 *.mkv *.mov"),
    ("Todos los archivos", "*.*"),
]
FILE_DECLARATION = (
    "Los participantes sabían que la reunión se estaba grabando y nadie se opuso a que la grabación "
    "se usara para generar la minuta."
)

ASSETS = BUNDLE / "minutas" / "assets"

# Colores de SeguriData; el verde de marca solo va en acentos porque el texto blanco sobre él no se lee bien
GREEN = "#81bb26"
DARK_GREEN = "#4f7a12"
DARK_GREEN_HOVER = "#3e6311"
SOFT_GREEN = "#eef6e1"
GRAY = "#5d676f"
TEXT = "#2b3036"
BORDER = "#dfe3dc"
BG = "#f4f6f2"
WHITE = "#ffffff"
RED = "#c62828"
DISABLED = "#b7c2ab"

FONT = "Segoe UI"


def audio_duration(path: str) -> float:
    """Duración en segundos; falla si el archivo no tiene audio que se pueda leer."""
    import av  # viene con faster-whisper

    with av.open(path) as container:
        if not container.streams.audio:
            raise ValueError("El archivo no tiene audio.")
        return (container.duration or 0) / 1_000_000


def parse_attendees(text: str) -> list[dict]:
    """Una persona por línea: 'Nombre <correo>' o solo 'correo'."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _EMAIL_RE.match(line)
        if not m:
            raise ValueError(f"Correo no válido: {line.strip()}")
        out.append({"name": (m.group(1) or "").strip(), "email": m.group(2)})
    return out


# ---------------------------------------------------------------- componentes con estilo


def primary_button(parent, text, command=None, **kw):
    return ctk.CTkButton(
        parent, text=text, command=command, height=42, corner_radius=8, font=(FONT, 14, "bold"),
        fg_color=DARK_GREEN, hover_color=DARK_GREEN_HOVER, text_color=WHITE, text_color_disabled=WHITE, **kw,
    )


def secondary_button(parent, text, command=None, **kw):
    kw.setdefault("height", 36)
    return ctk.CTkButton(
        parent, text=text, command=command, corner_radius=8, font=(FONT, 13), border_width=1,
        fg_color=WHITE, hover_color=SOFT_GREEN, border_color=BORDER, text_color=TEXT, **kw,
    )


def danger_button(parent, text, command=None, **kw):
    kw.setdefault("height", 36)
    return ctk.CTkButton(
        parent, text=text, command=command, corner_radius=8, font=(FONT, 13), border_width=1,
        fg_color=WHITE, hover_color="#fdecec", border_color="#f0c9c9", text_color=RED, **kw,
    )


def link_button(parent, text, command=None):
    return ctk.CTkButton(
        parent, text=text, command=command, width=0, height=30, corner_radius=6, font=(FONT, 13, "bold"),
        fg_color="transparent", hover_color=SOFT_GREEN, text_color=GRAY,
    )


def label(parent, text, size=13, weight="normal", color=TEXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=(FONT, size, weight), text_color=color, **kw)


def field_label(parent, text):
    return label(parent, text, size=13, weight="bold", anchor="w")


def card(parent, **kw):
    return ctk.CTkFrame(parent, fg_color=WHITE, corner_radius=10, border_width=1, border_color=BORDER, **kw)


def heading(parent, text):
    """Título de pantalla con la línea verde de la marca debajo."""
    box = ctk.CTkFrame(parent, fg_color="transparent")
    label(box, text, size=20, weight="bold").pack(anchor="w")
    ctk.CTkFrame(box, width=40, height=3, corner_radius=2, fg_color=GREEN).pack(anchor="w", pady=(4, 0))
    return box


def set_enabled(button: ctk.CTkButton, enabled: bool):
    button.configure(state="normal" if enabled else "disabled", fg_color=DARK_GREEN if enabled else DISABLED)


# ---------------------------------------------------------------- ventana


class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("light")
        super().__init__(fg_color=BG)
        self.title("Minutas · SeguriData")
        self.geometry("560x690")
        self.minsize(500, 600)
        self._set_icon(self)
        self._q: queue.Queue = queue.Queue()
        self.after(100, self._drain)

        self.api: api_mod.Api | None = None
        self.user: dict = {}
        self.events: list[dict] = []
        self.event_labels: list[str] = []
        self.recorder = None
        self.consent: dict | None = None
        self.started_at: datetime | None = None
        # Minutas que se transcriben en segundo plano, una a la vez (comparten el modelo de Whisper)
        self.jobs: list[dict] = []
        self._job_q: queue.Queue = queue.Queue()
        self.jobs_box: ctk.CTkFrame | None = None
        threading.Thread(target=self._job_worker, daemon=True).start()
        # Audio cifrado de grabaciones que no terminaron (cierre forzado): ya no se puede leer, se borra
        threading.Thread(target=self._cleanup_orphans, daemon=True).start()

        logo = Image.open(ASSETS / "logo.png")
        self.logo_small = ctk.CTkImage(logo, size=(118, 40))
        self.logo_large = ctk.CTkImage(logo, size=(230, 78))

        self.root_frame = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.root_frame.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._try_saved_session()

    # ------------------------------------------------------------ utilidades

    def ui(self, fn, *args):
        """Ejecuta fn en el hilo de la interfaz (tkinter no es thread-safe)."""
        self._q.put((fn, args))

    def _drain(self):
        while not self._q.empty():
            fn, args = self._q.get_nowait()
            fn(*args)
        self.after(100, self._drain)

    @staticmethod
    def _cleanup_orphans():
        from .recorder import cleanup_orphans

        try:
            cleanup_orphans()
        except Exception:
            pass  # limpieza de cortesía: nunca debe impedir que la app abra

    @staticmethod
    def _set_icon(window):
        try:
            window.iconbitmap(str(ASSETS / "icon.ico"))
        except Exception:
            pass  # sin ícono en sistemas que no soportan .ico

    def _screen(self, header: str = "full") -> ctk.CTkFrame:
        """Limpia la ventana y devuelve el contenedor de la pantalla nueva.

        header: "full" (con usuario y accesos), "brand" (solo logo) o "none".
        """
        for w in self.root_frame.winfo_children():
            w.destroy()
        if header != "none":
            bar = ctk.CTkFrame(self.root_frame, fg_color=WHITE, corner_radius=0, height=64)
            bar.pack(fill="x")
            bar.pack_propagate(False)
            ctk.CTkLabel(bar, image=self.logo_small, text="").pack(side="left", padx=(20, 12))
            ctk.CTkFrame(bar, width=1, height=28, fg_color=BORDER).pack(side="left")
            label(bar, "Minutas", size=15, weight="bold", color=GRAY).pack(side="left", padx=12)
            if header == "full":
                link_button(bar, "Salir", self._logout).pack(side="right", padx=(0, 14))
                link_button(bar, "Mis minutas ↗", lambda: webbrowser.open(WEB_URL)).pack(side="right", padx=4)
            ctk.CTkFrame(self.root_frame, height=3, corner_radius=0, fg_color=GREEN).pack(fill="x")
        body = ctk.CTkFrame(self.root_frame, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=24, pady=20)
        return body

    def _bg(self, work, on_ok, on_err=None):
        def run():
            try:
                result = work()
            except api_mod.AuthError:
                self.ui(self._session_expired)
                return
            except Exception as exc:
                self.ui(on_err or (lambda e: messagebox.showerror("Error", str(e))), exc)
                return
            self.ui(on_ok, result)

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ sesión

    def _try_saved_session(self):
        token = api_mod.load_token()
        if not token:
            self._show_login()
            return
        self.api = api_mod.Api(token)
        self._show_loading()
        self._bg(self.api.me, self._logged_in, lambda e: self._show_login(str(e)))

    def _show_loading(self):
        body = self._screen("none")
        label(body, "Cargando…", color=GRAY).place(relx=0.5, rely=0.5, anchor="center")

    def _show_login(self, error: str = ""):
        body = self._screen("none")
        box = card(body)
        box.place(relx=0.5, rely=0.46, anchor="center", relwidth=1.0)
        ctk.CTkFrame(box, height=4, corner_radius=2, fg_color=GREEN).pack(fill="x", padx=1, pady=(1, 0))
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=36, pady=(28, 30))

        ctk.CTkLabel(inner, image=self.logo_large, text="").pack()
        label(inner, "Minutas de reuniones", size=20, weight="bold").pack(pady=(22, 6))
        label(
            inner,
            "Genera minutas con los acuerdos de tus reuniones\nde Google Meet, Teams o Zoom.",
            color=GRAY, justify="center",
        ).pack()
        btn = primary_button(inner, "Iniciar sesión con Google")
        btn.pack(fill="x", pady=(22, 6))
        label(inner, "Uso interno. Inicia sesión con tu cuenta @seguridata.com.", size=11, color=GRAY).pack()
        status = label(inner, error, size=12, color=RED, wraplength=380, justify="center")
        status.pack(pady=(4, 0))

        def login():
            set_enabled(btn, False)
            status.configure(text="Completa el inicio de sesión en tu navegador…", text_color=GRAY)

            def work():
                self.api = api_mod.Api(api_mod.browser_login())
                return self.api.me()

            def fail(e):
                set_enabled(btn, True)
                status.configure(text=str(e), text_color=RED)

            self._bg(work, self._logged_in, fail)

        btn.configure(command=login)

    def _session_expired(self):
        api_mod.clear_token()
        if self.recorder is None:
            self._show_login("Tu sesión expiró. Vuelve a iniciar sesión.")

    def _logged_in(self, user: dict):
        self.user = user
        self._show_setup()

    def _logout(self):
        api_mod.clear_token()
        self.api, self.user = None, {}
        self._show_login()

    # ------------------------------------------------------------ preparar reunión

    def _show_setup(self):
        body = self._screen("full")
        name = (self.user.get("name") or self.user.get("email", "")).split(" ")[0]
        label(body, f"Hola, {name}", color=GRAY).pack(anchor="w")
        heading(body, "Nueva minuta").pack(anchor="w", pady=(0, 12))

        # El botón queda fijo abajo; lo demás se desplaza si las minutas en proceso no caben en pantallas bajas
        secondary_button(body, "Usar una grabación existente…", self._from_file).pack(side="bottom", fill="x", pady=(8, 0))
        primary_button(body, "Iniciar minuta", self._ask_consent).pack(side="bottom", fill="x", pady=(12, 0))
        scroll = ctk.CTkScrollableFrame(
            body, fg_color="transparent", corner_radius=0,
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=GRAY,
        )
        scroll.pack(fill="both", expand=True)
        self._jobs_panel(scroll).pack(fill="x", padx=(0, 4))
        form = card(scroll)
        form.pack(fill="x", padx=(0, 4))
        inner = ctk.CTkFrame(form, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=14)

        field_label(inner, "Reunión de tu calendario").pack(anchor="w")
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self.event_cb = ctk.CTkComboBox(
            row, values=["Cargando…"], state="readonly", height=36, corner_radius=8, font=(FONT, 13),
            dropdown_font=(FONT, 13), border_color=BORDER, button_color=BORDER, button_hover_color=GREEN,
            fg_color=WHITE, text_color=TEXT, dropdown_fg_color=WHITE, dropdown_hover_color=SOFT_GREEN,
            command=lambda _: self._event_selected(),
        )
        self.event_cb.pack(side="left", fill="x", expand=True)
        secondary_button(row, "↻", self._load_events, width=40).pack(side="left", padx=(8, 0))

        field_label(inner, "Título").pack(anchor="w", pady=(10, 0))
        self.title_var = ctk.StringVar()
        ctk.CTkEntry(
            inner, textvariable=self.title_var, height=36, corner_radius=8, font=(FONT, 13),
            border_color=BORDER, fg_color=WHITE, text_color=TEXT,
        ).pack(fill="x", pady=(4, 0))

        field_label(inner, "Plataforma").pack(anchor="w", pady=(10, 0))
        self.platform_var = ctk.StringVar(value="Google Meet")
        ctk.CTkSegmentedButton(
            inner, values=list(PLATFORMS), variable=self.platform_var, height=34, corner_radius=8,
            # Seleccionado en verde claro: el control usa un solo color de texto y debe leerse en ambos estados
            font=(FONT, 12), fg_color=BORDER, unselected_color=WHITE, unselected_hover_color=SOFT_GREEN,
            selected_color="#cfe6a8", selected_hover_color="#c2df93", text_color=TEXT,
        ).pack(fill="x", pady=(4, 0))

        field_label(inner, "Asistentes").pack(anchor="w", pady=(10, 0))
        label(inner, "Recibirán la minuta. Uno por línea: Nombre <correo>", size=12, color=GRAY).pack(anchor="w")
        self.attendees_txt = ctk.CTkTextbox(
            inner, height=96, corner_radius=8, border_width=1, border_color=BORDER, fg_color=WHITE,
            text_color=TEXT, font=(FONT, 13), wrap="none",
        )
        self.attendees_txt.pack(fill="x", pady=(4, 0))

        self._load_events()

    def _load_events(self):
        self.event_cb.configure(values=["Cargando…"])
        self.event_cb.set("Cargando…")

        def ok(events):
            self.events = events
            now = datetime.now(timezone.utc)
            labels = []
            default = 0
            for i, e in enumerate(events):
                start = datetime.fromisoformat(e["start"]).astimezone()
                end = datetime.fromisoformat(e["end"]).astimezone()
                en_curso = "  ● en curso" if start <= now < end else ""
                labels.append(f"{start:%H:%M}–{end:%H:%M} · {e['title']}{en_curso}")
                # Por defecto, la que acaba de empezar o empieza en unos minutos; si no, la siguiente
                if start <= now + timedelta(minutes=15):
                    default = i
            self.event_labels = labels + [NO_EVENT]
            self.event_cb.configure(values=self.event_labels)
            self.event_cb.set(self.event_labels[default])
            self._event_selected()

        def err(e):
            self.events = []
            self.event_labels = [NO_EVENT]
            self.event_cb.configure(values=self.event_labels)
            self.event_cb.set(NO_EVENT)

        self._bg(self.api.current_events, ok, err)

    def _event_selected(self):
        value = self.event_cb.get()
        idx = self.event_labels.index(value) if value in self.event_labels else -1
        if idx < 0 or idx >= len(self.events):
            return
        ev = self.events[idx]
        self.title_var.set(ev["title"])
        self.platform_var.set(PLATFORM_LABEL.get(ev["platform"], "Otra"))
        self.attendees_txt.delete("1.0", "end")
        self.attendees_txt.insert(
            "1.0", "\n".join(f"{a['name']} <{a['email']}>" if a["name"] else a["email"] for a in ev["attendees"])
        )

    # ------------------------------------------------------------ consentimiento

    def _form_ok(self) -> bool:
        if not self.title_var.get().strip():
            messagebox.showwarning("Falta el título", "Escribe el título de la reunión.")
            return False
        try:
            self.attendees = parse_attendees(self.attendees_txt.get("1.0", "end"))
        except ValueError as e:
            messagebox.showwarning("Asistentes", str(e))
            return False
        return True

    def _ask_consent(self):
        if not self._form_ok():
            return

        notice = consent_notice(self.user.get("name") or self.user["email"])
        dlg = ctk.CTkToplevel(self, fg_color=BG)
        dlg.title("Aviso a los participantes")
        dlg.geometry("540x430")
        dlg.resizable(False, False)
        dlg.transient(self)
        # CTkToplevel pone su propio ícono y aparece detrás si se hace grab de inmediato
        dlg.after(250, lambda: (self._set_icon(dlg), dlg.lift(), dlg.focus_force(), dlg.grab_set()))

        frm = ctk.CTkFrame(dlg, fg_color="transparent")
        frm.pack(fill="both", expand=True, padx=24, pady=20)
        heading(frm, "Antes de grabar, avisa a todos").pack(anchor="w")
        label(
            frm, "Copia este aviso, pégalo en el chat de la reunión y léelo en voz alta. Solo inicia si nadie se opone.",
            color=GRAY, wraplength=480, justify="left",
        ).pack(anchor="w", pady=(10, 10))

        box = ctk.CTkFrame(frm, fg_color=SOFT_GREEN, corner_radius=8, border_width=1, border_color="#d3e6b5")
        box.pack(fill="x")
        label(box, notice, wraplength=450, justify="left").pack(anchor="w", padx=14, pady=(12, 4))
        copy_btn = secondary_button(box, "Copiar aviso", height=32)
        copy_btn.configure(command=lambda: self._copy(notice, copy_btn))
        copy_btn.pack(anchor="e", padx=12, pady=(0, 12))

        announced = ctk.BooleanVar()
        no_objection = ctk.BooleanVar()
        btns = ctk.CTkFrame(frm, fg_color="transparent")
        start_btn = primary_button(btns, "Comenzar grabación")

        def refresh():
            set_enabled(start_btn, announced.get() and no_objection.get())

        check_kw = dict(
            font=(FONT, 13), text_color=TEXT, fg_color=DARK_GREEN, hover_color=DARK_GREEN_HOVER,
            border_color=GRAY, corner_radius=4, command=refresh,
        )
        ctk.CTkCheckBox(frm, text="Compartí el aviso en el chat y lo dije en voz alta", variable=announced, **check_kw).pack(
            anchor="w", pady=(16, 6)
        )
        ctk.CTkCheckBox(frm, text="Nadie se opuso a la grabación", variable=no_objection, **check_kw).pack(anchor="w")

        btns.pack(fill="x", pady=(22, 0))
        danger_button(btns, "Alguien se opuso: no grabar", dlg.destroy, height=42).pack(side="left")
        start_btn.pack(side="right")
        set_enabled(start_btn, False)

        def start():
            self.consent = {
                "aviso": notice,
                "confirmado_en": datetime.now(timezone.utc).isoformat(),
                "confirmado_por": self.user["email"],
                "nadie_se_opuso": True,
            }
            dlg.destroy()
            self._start_recording()

        start_btn.configure(command=start)

    def _copy(self, text: str, button: ctk.CTkButton | None = None):
        self.clipboard_clear()
        self.clipboard_append(text)
        if button is not None:
            original = button.cget("text")
            button.configure(text="Copiado ✓", text_color=DARK_GREEN)
            button.after(1500, lambda: button.winfo_exists() and button.configure(text=original, text_color=TEXT))

    # ------------------------------------------------------------ grabación

    def _start_recording(self):
        from .recorder import Recorder

        try:
            self.recorder = Recorder()
            self.recorder.start()
        except Exception as e:
            if self.recorder:
                self.recorder.discard()
            self.recorder = None
            messagebox.showerror("No se pudo grabar", str(e))
            return
        self.started_at = datetime.now(timezone.utc)
        self._t0 = time.monotonic()
        self._show_recording()

    def _show_recording(self):
        body = self._screen("brand")
        self.attributes("-topmost", True)

        box = card(body)
        box.pack(fill="x", pady=(10, 0))
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=24, pady=26)

        status = ctk.CTkFrame(inner, fg_color="transparent")
        status.pack()
        self.rec_dot = label(status, "●", size=16, color=RED)
        self.rec_dot.pack(side="left")
        label(status, "GRABANDO", size=13, weight="bold", color=RED).pack(side="left", padx=(6, 0))
        self.rec_label = label(inner, "00:00:00", size=44, weight="bold")
        self.rec_label.pack(pady=(4, 2))
        label(inner, self.title_var.get(), size=15, weight="bold", wraplength=440, justify="center").pack()
        label(
            inner,
            "Se graba el audio de la reunión y tu micrófono.\nEl audio se borra al terminar la transcripción.",
            size=12, color=GRAY, justify="center",
        ).pack(pady=(10, 0))

        late = secondary_button(body, "Copiar aviso otra vez (para quien se une tarde)")
        late.configure(command=lambda: self._copy(self.consent["aviso"], late))
        late.pack(fill="x", pady=(16, 0))
        primary_button(body, "Terminar y generar minuta", self._finish).pack(fill="x", pady=(10, 0))
        danger_button(body, "Cancelar y descartar (alguien se opuso)", self._cancel).pack(fill="x", pady=(10, 0))
        self._jobs_panel(body).pack(fill="x", pady=(16, 0))
        self._tick()

    def _tick(self):
        if self.recorder is None or not hasattr(self, "rec_label") or not self.rec_label.winfo_exists():
            return
        s = int(time.monotonic() - self._t0)
        self.rec_label.configure(text=f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        self.rec_dot.configure(text_color=RED if s % 2 == 0 else "#f3b0b0")
        self.after(1000, self._tick)

    def _cancel(self):
        if not messagebox.askyesno("Descartar", "Se detendrá la grabación y se borrará todo el audio. ¿Continuar?"):
            return
        self.recorder.stop()
        self.recorder.discard()
        self.recorder = None
        self.attributes("-topmost", False)
        self._show_setup()

    def _finish(self):
        recorder, self.recorder = self.recorder, None
        ended_at = datetime.now(timezone.utc)
        paths = recorder.stop()
        self.attributes("-topmost", False)

        self._enqueue(self._payload(self.started_at, ended_at, self.consent), paths, recorder)
        # La pantalla queda libre de inmediato para grabar la siguiente reunión
        self._show_setup()

    def _payload(self, started_at: datetime, ended_at: datetime, consent: dict) -> dict:
        return {
            "title": self.title_var.get().strip(),
            "platform": PLATFORMS.get(self.platform_var.get(), "otra"),
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "attendees": self.attendees,
            "consent": consent,
        }

    def _enqueue(self, payload: dict, paths: list[str], recorder=None):
        """recorder es None cuando el audio es un archivo del usuario: ese nunca se borra."""
        job = {
            "title": payload["title"], "state": "pending", "status": "En espera…", "progress": 0.0,
            "recorder": recorder, "paths": paths, "payload": payload, "transcript": None, "meeting": None,
        }
        self.jobs.append(job)
        self._job_q.put(job)

    # ------------------------------------------------------------ minuta desde un archivo

    def _selected_event(self) -> dict | None:
        value = self.event_cb.get()
        idx = self.event_labels.index(value) if value in self.event_labels else -1
        return self.events[idx] if 0 <= idx < len(self.events) else None

    def _from_file(self):
        if not self._form_ok():
            return
        path = filedialog.askopenfilename(parent=self, title="Elige la grabación de la reunión", filetypes=AUDIO_TYPES)
        if not path:
            return
        try:
            seconds = audio_duration(path)
        except Exception:
            messagebox.showerror("Archivo no válido", "No se pudo leer el audio de ese archivo.")
            return
        self._ask_file_consent(path, seconds)

    def _ask_file_consent(self, path: str, seconds: float):
        name = Path(path).name
        dlg = ctk.CTkToplevel(self, fg_color=BG)
        dlg.title("Minuta desde una grabación")
        dlg.geometry("520x390")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.after(250, lambda: (self._set_icon(dlg), dlg.lift(), dlg.focus_force(), dlg.grab_set()))

        frm = ctk.CTkFrame(dlg, fg_color="transparent")
        frm.pack(fill="both", expand=True, padx=24, pady=20)
        heading(frm, "Minuta desde una grabación").pack(anchor="w")

        box = ctk.CTkFrame(frm, fg_color=SOFT_GREEN, corner_radius=8, border_width=1, border_color="#d3e6b5")
        box.pack(fill="x", pady=(14, 0))
        label(box, name, weight="bold", wraplength=440, justify="left").pack(anchor="w", padx=14, pady=(10, 0))
        s = int(seconds)
        label(
            box, f"Duración {s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}  ·  {self.title_var.get().strip()}",
            size=12, color=GRAY, wraplength=440, justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 10))

        label(
            frm, "Usa solo grabaciones de reuniones en las que todos sabían que se grababa. "
            "El archivo se transcribe en este equipo y no se modifica ni se borra.",
            size=12, color=GRAY, wraplength=470, justify="left",
        ).pack(anchor="w", pady=(12, 0))

        agreed = ctk.BooleanVar()
        btns = ctk.CTkFrame(frm, fg_color="transparent")
        go = primary_button(btns, "Generar minuta")
        check = ctk.CTkCheckBox(
            frm, text=FILE_DECLARATION, variable=agreed, font=(FONT, 13), text_color=TEXT,
            fg_color=DARK_GREEN, hover_color=DARK_GREEN_HOVER, border_color=GRAY, corner_radius=4,
            command=lambda: set_enabled(go, agreed.get()),
        )
        check._text_label.configure(wraplength=420, justify="left")  # CTkCheckBox no parte el texto por sí solo
        check.pack(anchor="w", pady=(14, 0))

        btns.pack(fill="x", side="bottom")
        secondary_button(btns, "Cancelar", dlg.destroy, height=42, width=120).pack(side="left")
        go.pack(side="right")
        set_enabled(go, False)

        def start():
            ev = self._selected_event()
            if ev:
                started, ended = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])
            else:  # sin evento: la fecha del archivo se toma como el fin de la reunión
                ended = datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)
                started = ended - timedelta(seconds=seconds)
            consent = {
                "aviso": FILE_DECLARATION,
                "confirmado_en": datetime.now(timezone.utc).isoformat(),
                "confirmado_por": self.user["email"],
                "nadie_se_opuso": True,
                "origen": "archivo",
                "archivo": name,
            }
            self._enqueue(self._payload(started, ended, consent), [path])
            dlg.destroy()
            self._refresh_jobs()

        go.configure(command=start)

    # ------------------------------------------------------------ minutas en segundo plano

    def _job_worker(self):
        while True:
            job = self._job_q.get()
            try:
                self._run_job(job)
            except api_mod.AuthError:
                self._set_job(job, state="error", status="Tu sesión expiró. Inicia sesión y presiona Reintentar.")
                self.ui(self._session_expired)
            except Exception as e:
                self._set_job(job, state="error", status=str(e))

    def _run_job(self, job: dict):
        if job["transcript"] is None:
            from .transcriber import transcribe

            self._set_job(job, state="running", status="Cargando el modelo de transcripción…")
            last = [-1]

            def progress(p: float):
                if int(p * 100) != last[0]:  # no saturar la interfaz con cada segmento
                    last[0] = int(p * 100)
                    self._set_job(job, status=f"Transcribiendo… {p:.0%}", progress=p)

            try:
                transcript = transcribe(job["paths"], progress)
            finally:
                if job["recorder"]:
                    job["recorder"].discard()
            if not transcript.strip():
                job["transcript"] = ""
                raise RuntimeError("No se detectó voz en la grabación; no se generó la minuta.")
            job["transcript"] = transcript
        elif not job["transcript"]:
            raise RuntimeError("No se detectó voz en la grabación; no se generó la minuta.")

        self._set_job(job, state="running", status="Enviando la transcripción…", progress=1.0)
        job["meeting"] = self.api.create_meeting({**job["payload"], "transcript": job["transcript"]})
        msg = "Se enviará a los asistentes automáticamente." if self.user.get("auto_send") else "Lista para revisar en el portal."
        self._set_job(job, state="done", status=msg)

    def _set_job(self, job: dict, **changes):
        """Se llama desde el hilo de trabajo; la interfaz se actualiza en su propio hilo."""
        job.update(changes)
        self.ui(self._refresh_jobs)

    def _retry_job(self, job: dict):
        job.update(state="pending", status="En espera…")
        self._job_q.put(job)
        self._refresh_jobs()

    def _dismiss_job(self, job: dict):
        self.jobs.remove(job)
        self._refresh_jobs()

    def _jobs_panel(self, parent) -> ctk.CTkFrame:
        self.jobs_box = ctk.CTkFrame(parent, fg_color="transparent")
        self._refresh_jobs()
        return self.jobs_box

    def _refresh_jobs(self):
        box = self.jobs_box
        if box is None or not box.winfo_exists():
            return
        for w in box.winfo_children():
            w.destroy()
        if not self.jobs:
            box.configure(height=1)  # sin minutas en proceso el panel no ocupa espacio
            return

        panel = card(box)
        panel.pack(fill="x", pady=(0, 12))
        inner = ctk.CTkFrame(panel, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=(8, 10))
        label(inner, "Minutas en proceso", size=12, weight="bold", color=GRAY).pack(anchor="w")
        for job in self.jobs:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(4, 0))
            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            color = {"error": RED, "done": DARK_GREEN}.get(job["state"], GRAY)
            line = ctk.CTkFrame(info, fg_color="transparent")
            line.pack(anchor="w")
            label(line, job["title"], size=13, weight="bold").pack(side="left")
            label(line, f"  ·  {job['status']}", size=12, color=color, wraplength=300, justify="left").pack(side="left")
            if job["state"] == "running":
                bar = ctk.CTkProgressBar(info, height=6, corner_radius=3, progress_color=GREEN, fg_color=BORDER)
                bar.set(job["progress"])
                bar.pack(fill="x", pady=(2, 0))
            elif job["state"] == "done":
                url = f"{WEB_URL}/#/reuniones/{job['meeting']['id']}"
                link_button(row, "✕", lambda j=job: self._dismiss_job(j)).pack(side="right")
                secondary_button(row, "Abrir", lambda u=url: webbrowser.open(u), width=70, height=28).pack(side="right")
            elif job["state"] == "error":
                link_button(row, "✕", lambda j=job: self._dismiss_job(j)).pack(side="right")
                if job["transcript"]:  # falló el envío: la transcripción no se pierde
                    secondary_button(row, "Reintentar", lambda j=job: self._retry_job(j), width=90, height=28).pack(
                        side="right"
                    )

    def _on_close(self):
        pending = [j for j in self.jobs if j["state"] in ("pending", "running")]
        if self.recorder is not None:
            if not messagebox.askyesno("Grabación en curso", "Hay una grabación en curso. ¿Descartarla y salir?"):
                return
            self.recorder.stop()
            self.recorder.discard()
        elif pending and not messagebox.askyesno(
            "Minutas en proceso",
            f"Hay {len(pending)} minuta(s) generándose. Si cierras no se generarán. ¿Salir de todos modos?",
        ):
            return
        for job in pending:
            try:
                if job["recorder"]:
                    job["recorder"].discard()
            except OSError:
                pass  # Windows no deja borrar el audio que se está leyendo; queda en la carpeta temporal
        self.destroy()
