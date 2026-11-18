from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./minutas.db"

    # OAuth de Google Workspace (pantalla de consentimiento de tipo "Interno")
    google_client_id: str
    google_client_secret: str
    allowed_domain: str = "seguridata.com"

    backend_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"

    # Firma de los JWT de sesión y cifrado de los refresh tokens de Google
    secret_key: str
    fernet_key: str
    session_hours: int = 12

    # Vercel define VERCEL=1. Ahí no se puede trabajar después de responder, así que la minuta se
    # genera dentro de la misma petición (hasta el maxDuration de vercel.json)
    vercel: bool = False

    # Proveedor de IA para generar las minutas: "anthropic" (Claude) u "openai" (ChatGPT).
    # Vacío = Anthropic si hay ANTHROPIC_API_KEY; si no, OpenAI si hay OPENAI_API_KEY.
    ai_provider: str = ""

    # Si se deja vacía, el SDK de Anthropic la toma de la variable de entorno ANTHROPIC_API_KEY
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5-5"

    openai_api_key: str = ""
    openai_model: str = "gpt-5.5"

    @property
    def provider(self) -> str:
        chosen = self.ai_provider.strip().lower()
        if chosen in ("anthropic", "claude"):
            return "anthropic"
        if chosen in ("openai", "chatgpt"):
            return "openai"
        if chosen:
            raise ValueError(f"AI_PROVIDER no reconocido: {self.ai_provider!r} (usa anthropic u openai)")
        if not self.anthropic_api_key and self.openai_api_key:
            return "openai"
        return "anthropic"

    # Correos (separados por coma) que pueden ver el consumo de IA de toda la empresa
    admin_emails: str = ""

    # Monday: cada persona conecta su propia cuenta desde su perfil, pegando su token personal de Monday
    # (se guarda cifrado). MONDAY_ENABLED=false apaga la integración para todos.
    monday_enabled: bool = True
    # Opcional: app OAuth registrada en Monday. Con estos dos valores aparece además el botón "Conectar Monday",
    # que evita copiar el token a mano.
    monday_client_id: str = ""
    monday_client_secret: str = ""

    @property
    def monday_oauth(self) -> bool:
        return self.monday_enabled and bool(self.monday_client_id and self.monday_client_secret)

    @property
    def monday_redirect_uri(self) -> str:
        return f"{self.backend_url.rstrip('/')}/auth/monday/callback"

    def is_admin(self, email: str) -> bool:
        return email.lower() in {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
