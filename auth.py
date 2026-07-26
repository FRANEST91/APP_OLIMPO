import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from db import get_conn

logger = logging.getLogger("olimpo.auth")

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


SESSION_TTL_SECONDS = 60 * 60


def abrir_sesion(tg_id: int, session_id: str, ip: str | None, ttl_seconds: int = SESSION_TTL_SECONDS) -> bool:
    """Registra la sesión activa de tg_id, reemplazando cualquier otra que
    hubiera (una sola sesión activa por cuenta a la vez — dos personas ya
    no pueden usar la misma cuenta al mismo tiempo sin que se note).

    Devuelve True si había otra sesión vigente (para poder avisarle al
    dueño que se cerró)."""
    ahora = datetime.now(timezone.utc)
    expira = ahora + timedelta(seconds=ttl_seconds)
    with get_conn() as conn:
        anterior = conn.execute(
            "SELECT expires_at FROM sesiones WHERE tg_id = ?", (tg_id,)
        ).fetchone()
        habia_otra = bool(
            anterior and datetime.fromisoformat(anterior["expires_at"]) > ahora
        )
        conn.execute(
            """
            INSERT INTO sesiones (tg_id, session_id, ip, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                session_id = excluded.session_id, ip = excluded.ip,
                created_at = excluded.created_at, expires_at = excluded.expires_at
            """,
            (tg_id, session_id, ip, ahora.isoformat(), expira.isoformat()),
        )
    return habia_otra


def sesion_vigente(tg_id: int, session_id: str) -> bool:
    """False si otra sesión reemplazó a esta (login desde otro lado), si un
    admin la cerró a mano, o si venció."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT session_id, expires_at FROM sesiones WHERE tg_id = ?", (tg_id,)
        ).fetchone()
    if row is None or row["session_id"] != session_id:
        return False
    return datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)


def cerrar_sesion(tg_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM sesiones WHERE tg_id = ?", (tg_id,))


def listar_sesiones_activas() -> list:
    with get_conn() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM sesiones WHERE expires_at > ? ORDER BY created_at DESC",
                (datetime.now(timezone.utc).isoformat(),),
            )
        ]


def sesion_de(tg_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM sesiones WHERE tg_id = ? AND expires_at > ?",
            (tg_id, datetime.now(timezone.utc).isoformat()),
        ).fetchone()
    return dict(row) if row else None


def registrar_ip_si_cambio(tg_id: int, session_id: str, ip: str | None) -> bool:
    """Actualiza la IP guardada de la sesión si cambió respecto a la última
    vista. Devuelve True solo cuando de verdad cambió (para disparar el
    aviso una sola vez por cambio, no en cada rerun de la pestaña)."""
    if not ip:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ip FROM sesiones WHERE tg_id = ? AND session_id = ?",
            (tg_id, session_id),
        ).fetchone()
        if row is None or row["ip"] == ip:
            return False
        conn.execute(
            "UPDATE sesiones SET ip = ? WHERE tg_id = ? AND session_id = ?",
            (ip, tg_id, session_id),
        )
        cambio_real = row["ip"] is not None
    return cambio_real


def teclado_moderacion(tg_id: int) -> InlineKeyboardMarkup:
    """Botones de "🚫 Cerrar sesión" / "⛔ Revocar membresía" para pegar en
    cualquier alerta relacionada con un tg_id — así el admin actúa desde el
    mismo mensaje de la alerta, sin tener que ir a buscar al usuario."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚫 Cerrar sesión", callback_data=f"admin_kick:{tg_id}"),
        InlineKeyboardButton("⛔ Revocar membresía", callback_data=f"admin_ban:{tg_id}"),
    ]])


async def alertar_moderacion(bot: Bot, tg_id: int, mensaje: str) -> None:
    """Como sdk.alertar(), pero con los botones de moderación pegados —
    pensada para comportamiento sospechoso sobre un tg_id puntual, no para
    el log de rutina de cada módulo (esos no necesitan acción inmediata).

    Recibe el Bot ya abierto en vez de crear uno (a diferencia de
    sdk.alertar): se llama tanto desde app.py, que corre asyncio.run() por
    fuera, como desde bot_auth.py, que ya está corriendo dentro de un
    event loop — ahí un asyncio.run() propio tiraría error.
    """
    canal = os.getenv("OLIMPO_LOG_CHANNEL_ID")
    destinos = [canal] if canal else list_admin_ids()
    teclado = teclado_moderacion(tg_id)
    for chat_id in destinos:
        try:
            await bot.send_message(
                chat_id=chat_id, text=mensaje, parse_mode=ParseMode.HTML, reply_markup=teclado,
            )
        except Exception:
            logger.exception("No se pudo mandar la alerta de moderación a %s", chat_id)


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
