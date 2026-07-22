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
        f"Tu ID: <code>{tg_id}</code>\n\n"
        "¿Aún no tienes acceso a Olimpo? Pide informes con @MrMxyzptlk04 y @Chack0071."
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
