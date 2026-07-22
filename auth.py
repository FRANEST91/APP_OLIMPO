import os
import secrets
import time
from datetime import datetime, timezone

from telegram import Bot
from telegram.constants import ParseMode

from db import get_conn

OTP_TTL_SECONDS = 60
MAX_ATTEMPTS = 3

# OTPs activos: {tg_id: {"code": str, "expires_at": float, "attempts": int}}
_pending: dict = {}


async def send_otp(tg_id: int, bot: Bot) -> str:
    code = str(secrets.randbelow(900000) + 100000)  # 6 dígitos
    _pending[tg_id] = {
        "code": code,
        "expires_at": time.time() + OTP_TTL_SECONDS,
        "attempts": 0,
    }
    await bot.send_message(
        chat_id=tg_id,
        text=f"🔐 Tu código: <code>{code}</code>\nVence en 1 minuto.",
        parse_mode=ParseMode.HTML,
    )
    return code


def verify_otp(tg_id: int, entered: str) -> bool:
    entry = _pending.get(tg_id)
    if not entry:
        return False
    entry["attempts"] += 1
    if entry["attempts"] > MAX_ATTEMPTS:
        _pending.pop(tg_id, None)
        return False
    if time.time() > entry["expires_at"]:
        _pending.pop(tg_id, None)
        return False
    ok = entered.strip() == entry["code"]
    if ok:
        _pending.pop(tg_id, None)
    return ok


def is_admin(tg_id: int) -> bool:
    ids = [x.strip() for x in os.getenv("OLIMPO_ADMINS", "").split(",") if x.strip()]
    return str(tg_id) in ids


def is_whitelisted(tg_id: int) -> bool:
    # Los admins siempre tienen acceso, aunque no estén (todavía) en la
    # tabla whitelist — evita que se bloqueen a sí mismos al gestionarla.
    if is_admin(tg_id):
        return True
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM whitelist WHERE tg_id = ? AND active = 1", (tg_id,)
        ).fetchone()
    return row is not None


def add_user(tg_id: int, username: str | None, added_by: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO whitelist (tg_id, username, active, added_by, added_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                username = excluded.username, active = 1,
                added_by = excluded.added_by, added_at = excluded.added_at
            """,
            (tg_id, username, added_by, now),
        )


def remove_user(tg_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE whitelist SET active = 0 WHERE tg_id = ?", (tg_id,))


def list_users() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM whitelist ORDER BY active DESC, added_at DESC"
        ).fetchall()


def import_csv(rows: list, added_by: int) -> tuple:
    """Importa filas tipo {'tg_id':.., 'username':.., 'active':..}.

    Solo requiere la columna tg_id; username y active son opcionales
    (active ausente o distinto de 0/false/False se trata como activo).
    Devuelve (importados, omitidos).
    """
    importados = 0
    omitidos = 0
    for row in rows:
        raw_id = (row.get("tg_id") or "").strip()
        if not raw_id.isdigit():
            omitidos += 1
            continue
        activo = str(row.get("active", "1")).strip().lower()
        if activo in ("0", "false"):
            omitidos += 1
            continue
        username = (row.get("username") or "").strip() or None
        add_user(int(raw_id), username, added_by)
        importados += 1
    return importados, omitidos
