import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("OLIMPO_DB_PATH", "olimpo.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tempmail_cuentas (
    user_id     INTEGER PRIMARY KEY,
    email       TEXT NOT NULL,
    password    TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    token       TEXT,
    token_at    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS olimpo_sms_orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    order_id      TEXT NOT NULL UNIQUE,
    phone_number  TEXT NOT NULL,
    service_name  TEXT NOT NULL,
    country_name  TEXT NOT NULL,
    sms_code      TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    requested_at  TEXT NOT NULL,
    completed_at  TEXT
);

CREATE TABLE IF NOT EXISTS whitelist (
    tg_id      INTEGER PRIMARY KEY,
    username   TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    added_by   INTEGER,
    added_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS carrusel (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre        TEXT NOT NULL,
    contenido     BLOB NOT NULL,
    mime_type     TEXT NOT NULL,
    orden         INTEGER NOT NULL DEFAULT 0,
    duracion_ms   INTEGER NOT NULL DEFAULT 4000,
    active        INTEGER NOT NULL DEFAULT 1,
    uploaded_at   TEXT NOT NULL,
    texto_arriba  TEXT,
    texto_abajo   TEXT
);

CREATE TABLE IF NOT EXISTS creditos (
    user_id  INTEGER PRIMARY KEY,
    saldo    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS creditos_movimientos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    delta       INTEGER NOT NULL,
    motivo      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrar(conn: sqlite3.Connection) -> None:
    # CREATE TABLE IF NOT EXISTS no agrega columnas a una tabla que ya
    # existía de un deploy anterior sin ellas — hay que sumarlas a mano.
    columnas = {row["name"] for row in conn.execute("PRAGMA table_info(carrusel)")}
    if "texto_arriba" not in columnas:
        conn.execute("ALTER TABLE carrusel ADD COLUMN texto_arriba TEXT")
    if "texto_abajo" not in columnas:
        conn.execute("ALTER TABLE carrusel ADD COLUMN texto_abajo TEXT")


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrar(conn)
