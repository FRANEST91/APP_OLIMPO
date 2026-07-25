import os
import secrets
from datetime import datetime, timedelta, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from db import get_conn

LOGIN_TTL_SECONDS = 120
RESEND_COOLDOWN_SECONDS = 30


class LoginCooldown(Exception):
    """Ya hay una solicitud de acceso vigente para este tg_id."""


async def crear_solicitud(tg_id: int, bot: Bot) -> str:
    """Login sin código tipeado: se manda un mensaje con botones a Telegram
    y hay que tocar "Fui yo, entrar" desde ahí. Un código de 6 dígitos (o
    un link) se puede copiar y reenviar a cualquiera con el mismo efecto
    que prestarle la sesión — esto obliga a que la confirmación pase por
    una acción en vivo dentro de la cuenta real de Telegram, no por un
    valor que se pueda pegar en otro chat.
    """
    ahora = datetime.now(timezone.utc)

    with get_conn() as conn:
        # Limpieza liviana: sin esto la tabla crece para siempre, no hay
        # cron ni proceso aparte que la pode.
        conn.execute(
            "DELETE FROM login_requests WHERE created_at < ?",
            ((ahora - timedelta(days=1)).isoformat(),),
        )

        pendiente = conn.execute(
            """
            SELECT created_at, expires_at FROM login_requests
            WHERE tg_id = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
            """,
            (tg_id,),
        ).fetchone()
        if pendiente and datetime.fromisoformat(pendiente["expires_at"]) > ahora:
            creado = datetime.fromisoformat(pendiente["created_at"])
            if (ahora - creado).total_seconds() < RESEND_COOLDOWN_SECONDS:
                raise LoginCooldown()

        token = secrets.token_urlsafe(24)
        expira = ahora + timedelta(seconds=LOGIN_TTL_SECONDS)
        conn.execute(
            """
            INSERT INTO login_requests (token, tg_id, status, created_at, expires_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (token, tg_id, ahora.isoformat(), expira.isoformat()),
        )

    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Fui yo, entrar", callback_data=f"login_ok:{token}"),
        InlineKeyboardButton("🚫 No fui yo", callback_data=f"login_no:{token}"),
    ]])
    await bot.send_message(
        chat_id=tg_id,
        text=(
            "🔐 <b>Solicitud de acceso a OLIMPO</b>\n"
            "Alguien está intentando entrar con tu cuenta. Si fuiste tú, "
            "confirma abajo. Vence en 2 minutos."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=teclado,
    )
    return token


def estado_solicitud(token: str) -> str:
    """'pending' | 'confirmed' | 'denied' | 'expired' | 'not_found'."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, expires_at FROM login_requests WHERE token = ?", (token,)
        ).fetchone()
    if row is None:
        return "not_found"
    if row["status"] == "pending" and datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return "expired"
    return row["status"]


def resolver_solicitud(token: str, presser_tg_id: int, aceptar: bool) -> tuple[str, int | None]:
    """Llamado desde el bot cuando alguien toca uno de los botones.

    Devuelve (resultado, tg_id_dueño). resultado es uno de:
    "ok" | "no_autorizado" | "ya_resuelto" | "vencido" | "not_found".

    El chequeo de que quien tocó el botón (presser_tg_id) sea el mismo
    tg_id al que le mandamos la solicitud pasa ANTES de tocar el estado en
    la base — así, si alguien reenvía el mensaje con los botones a otro
    chat, quien lo toque ahí no puede resolver la solicitud ajena.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT tg_id, status, expires_at FROM login_requests WHERE token = ?", (token,)
        ).fetchone()
        if row is None:
            return "not_found", None
        if row["tg_id"] != presser_tg_id:
            return "no_autorizado", row["tg_id"]
        if row["status"] != "pending":
            return "ya_resuelto", row["tg_id"]
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            conn.execute("UPDATE login_requests SET status = 'denied' WHERE token = ?", (token,))
            return "vencido", row["tg_id"]

        nuevo_status = "confirmed" if aceptar else "denied"
        conn.execute("UPDATE login_requests SET status = ? WHERE token = ?", (nuevo_status, token))
    return "ok", row["tg_id"]


def list_admin_ids() -> list[int]:
    ids = []
    for raw in os.getenv("OLIMPO_ADMINS", "").split(","):
        raw = raw.strip()
        if raw.lstrip("-").isdigit():
            ids.append(int(raw))
    return ids


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
