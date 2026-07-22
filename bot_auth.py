import logging
import os

from dotenv import load_dotenv

load_dotenv()

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("olimpo.bot_auth")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_id = update.effective_user.id
    await update.message.reply_html(
        "🔥 <b>OLIMPO</b>\n\n"
        f"Tu Telegram ID es: <code>{tg_id}</code>\n\n"
        "Pídele al administrador que lo agregue a la whitelist. Una vez "
        "autorizado, ingresa este ID en la app para recibir tu código de acceso."
    )


def main() -> None:
    token = os.environ["OLIMPO_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whoami", start))
    logger.info("OLIMPO auth bot iniciado")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
