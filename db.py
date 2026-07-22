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


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
