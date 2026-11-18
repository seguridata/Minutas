from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

def normalize_url(url: str) -> str:
    """Neon, Vercel y Heroku dan postgres:// o postgresql://; SQLAlchemy necesita el driver psycopg."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


_url = normalize_url(get_settings().database_url)
if _url.startswith("sqlite"):
    engine = create_engine(_url, connect_args={"check_same_thread": False})
else:
    # Las funciones de Vercel se congelan entre peticiones: se revisa la conexión antes de usarla
    engine = create_engine(_url, pool_pre_ping=True, pool_size=2, max_overflow=3, pool_recycle=300)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
