r"""Copia todo lo de la base SQLite local (minutas.db) a la base Postgres de producción.

Se corre una sola vez desde la carpeta backend, con el mismo .env (la misma FERNET_KEY que usará Vercel):

    .venv\Scripts\python copiar_a_postgres.py "postgresql://usuario:clave@servidor/base?sslmode=require"

Crea las tablas si faltan y se niega a seguir si la base destino ya tiene datos, para no duplicar nada.
Si la base destino solo tiene pruebas (por ejemplo, el usuario que se creó al entrar por primera vez),
agrega --vaciar-destino al final para borrarlas antes de copiar.
Lo cifrado se descifra con FERNET_KEY y se vuelve a cifrar con la misma llave al guardarlo.
"""

import sys

from sqlalchemy import create_engine, func, select, text

from app.db import Base, engine as origen, normalize_url
from app import models  # noqa: F401  registra las tablas en Base.metadata


def main(url_destino: str, vaciar: bool = False) -> None:
    # Por si se pega la línea completa del .env (DATABASE_URL="...")
    url_destino = url_destino.strip().removeprefix("DATABASE_URL=").strip('"\'')
    destino = create_engine(normalize_url(url_destino))
    if destino.url == origen.url:
        sys.exit("El destino es la misma base que el origen")
    Base.metadata.create_all(destino)
    tablas = Base.metadata.sorted_tables  # en orden de dependencias: primero users

    with destino.connect() as conn:
        conteo = {t.name: conn.execute(select(func.count()).select_from(t)).scalar() for t in tablas}
    ocupadas = ", ".join(f"{k} ({v})" for k, v in conteo.items() if v)
    if ocupadas and not vaciar:
        sys.exit(f"La base destino ya tiene datos en: {ocupadas}. No se copió nada.\n"
                 "Si son solo pruebas, vuelve a correrlo agregando --vaciar-destino al final.")

    # Todo en una sola transacción: si algo falla, la base destino queda como estaba
    with origen.connect() as src, destino.begin() as dst:
        if ocupadas:
            print("Vaciando la base destino:", ocupadas)
            for tabla in reversed(tablas):
                dst.execute(tabla.delete())
        for tabla in tablas:
            filas = [dict(r._mapping) for r in src.execute(select(tabla))]
            if filas:
                dst.execute(tabla.insert(), filas)
            print(f"{tabla.name}: {len(filas)}")
            # Postgres no avanza el contador de ids al insertar ids explícitos
            if destino.dialect.name == "postgresql" and filas and "id" in tabla.c:
                dst.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{tabla.name}', 'id'), (SELECT MAX(id) FROM {tabla.name}))"
                ))
    print("Listo.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--vaciar-destino"]
    if len(args) != 1:
        sys.exit(__doc__)
    main(args[0], vaciar="--vaciar-destino" in sys.argv)
