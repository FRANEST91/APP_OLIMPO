import logging
import os

from dotenv import load_dotenv

load_dotenv()

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("olimpo.bot_auth")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_id = update.effective_user.id
    await update.message.reply_html(
        "🔥 <b>OLIMPO</b>\n\n"
        f"Tu ID: <code>{tg_id}</code>\n\n"
        "¿Aún no tienes acceso a Olimpo? Pide informes con @MrMxyzptlk04 y @Chack0071."
    )


async def _notificar_admins(context: ContextTypes.DEFAULT_TYPE, mensaje: str) -> None:
    # No usa sdk.alertar() a propósito: esa función arma su propio Bot y
    # corre asyncio.run() por dentro, y este handler ya está corriendo
    # sobre el event loop del bot — llamar asyncio.run() ahí adentro
    # tira "cannot be called from a running event loop".
    canal = os.getenv("OLIMPO_LOG_CHANNEL_ID")
    destinos = [canal] if canal else auth.list_admin_ids()
    for chat_id in destinos:
        try:
            await context.bot.send_message(chat_id=chat_id, text=mensaje, parse_mode=ParseMode.HTML)
        except Exception:
            logger.warning("No se pudo notificar a %s", chat_id)


async def on_login_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    if ":" not in data:
        await query.answer()
        return

    accion, token = data.split(":", 1)
    if accion not in ("login_ok", "login_no"):
        await query.answer()
        return

    resultado, owner_tg_id = auth.resolver_solicitud(
        token, query.from_user.id, aceptar=(accion == "login_ok"),
    )

    if resultado == "no_autorizado":
        await query.answer("Esta solicitud no es tuya.", show_alert=True)
        if owner_tg_id:
            # A quien le mandamos el mensaje original NO fue quien tocó el
            # botón — probablemente reenvió el mensaje. Vale la pena que se
            # entere, incluso si fue sin mala intención.
            try:
                await context.bot.send_message(
                    chat_id=owner_tg_id,
                    text=(
                        "⚠️ <b>Alguien más intentó confirmar un acceso a Olimpo a tu nombre</b>\n"
                        f"Usuario: <code>{query.from_user.id}</code> "
                        f"(@{query.from_user.username or 'sin username'})\n"
                        "Si no reenviaste ese mensaje vos mismo, avisale a un admin."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                logger.warning("No se pudo avisar al dueño %s", owner_tg_id)
        return

    if resultado in ("not_found", "vencido", "ya_resuelto"):
        await query.answer("Este enlace ya no es válido o ya fue usado.", show_alert=True)
        return

    # resultado == "ok"
    if accion == "login_ok":
        await query.edit_message_text("✅ Acceso confirmado. Vuelve a la pestaña de Olimpo.")
        await query.answer()
    else:
        await query.edit_message_text("🚫 Acceso rechazado.")
        await query.answer()
        await _notificar_admins(
            context,
            f"🚫 <b>Acceso rechazado</b>\n👤 <code>{owner_tg_id}</code> rechazó un intento de acceso a Olimpo.",
        )


def main() -> None:
    token = os.environ["OLIMPO_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whoami", start))
    app.add_handler(CallbackQueryHandler(on_login_callback, pattern=r"^login_(ok|no):"))
    logger.info("OLIMPO auth bot iniciado")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
