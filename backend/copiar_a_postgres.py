"""Copia todo lo de la base SQLite local (minutas.db) a la base Postgres de producción.

Se corre una sola vez desde la carpeta backend, con el mismo .env (la misma FERNET_KEY que usará Vercel):

    .venv\\Scripts\\python copiar_a_postgres.py "postgresql://usuario:clave@servidor/base?sslmode=require"

Crea las tablas si faltan y se niega a seguir si la base destino ya tiene datos, para no duplicar nada.
Lo cifrado se descifra con FERNET_KEY y se vuelve a cifrar con la misma llave al guardarlo.
"""

import sys

from sqlalchemy import create_engine, func, select, text

from app.db import Base, engine as origen, normalize_url
from app import models  # noqa: F401  registra las tablas en Base.metadata


def main(url_destino: str) -> None:
    destino = create_engine(normalize_url(url_destino))
    if destino.url == origen.url:
        sys.exit("El destino es la misma base que el origen")
    Base.metadata.create_all(destino)
    tablas = Base.metadata.sorted_tables  # en orden de dependencias: primero users

    with destino.connect() as conn:
        ocupadas = [t.name for t in tablas if conn.execute(select(func.count()).select_from(t)).scalar()]
    if ocupadas:
        sys.exit(f"La base destino ya tiene datos en: {', '.join(ocupadas)}. No se copió nada.")

    with origen.connect() as src, destino.begin() as dst:
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
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
