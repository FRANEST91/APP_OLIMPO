import csv
import io
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import auth
import canal
import sdk

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("olimpo.bot_auth")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_id = update.effective_user.id
    await update.message.reply_html(
        "🔥 <b>OLIMPO</b>\n\n"
        f"Tu ID: <code>{tg_id}</code>\n\n"
        "¿Aún no tienes acceso a Olimpo? Pide informes con @MrMxyzptlk04 y @Chack0071."
    )


def _fmt_dt(iso: str) -> str:
    # Timestamps guardados en UTC ISO — se muestran tal cual, sin pretender
    # convertir a la zona horaria de nadie en particular.
    return iso.replace("T", " ").split(".")[0] + " UTC"


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
            # Con botones de moderación sobre el dueño: si esto se repite,
            # probablemente su link está circulando.
            await auth.alertar_moderacion(
                context.bot, owner_tg_id,
                f"⚠️ <b>Login ajeno rechazado</b>\n"
                f"👤 Cuenta: <code>{owner_tg_id}</code>\n"
                f"🙋 Quien tocó el botón: <code>{query.from_user.id}</code> "
                f"(@{query.from_user.username or 'sin username'})\n"
                "Probablemente reenvió el mensaje de acceso — revisa si es normal.",
            )
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
        await auth.alertar_moderacion(
            context.bot, owner_tg_id,
            f"🚫 <b>Acceso rechazado</b>\n👤 <code>{owner_tg_id}</code> rechazó un intento de acceso a Olimpo.",
        )


async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botones "🚫 Cerrar sesión" / "⛔ Revocar membresía" pegados a las
    alertas de seguridad (o mostrados por /admin y /usuario) — permite
    actuar en el momento sin salir de Telegram."""
    query = update.callback_query
    data = query.data or ""
    if ":" not in data:
        await query.answer()
        return

    accion, tg_id_str = data.split(":", 1)
    if accion not in ("admin_kick", "admin_ban") or not tg_id_str.lstrip("-").isdigit():
        await query.answer()
        return

    if not auth.is_admin(query.from_user.id):
        await query.answer("Esto es solo para admins.", show_alert=True)
        return

    tg_id = int(tg_id_str)
    quien = query.from_user.id

    if accion == "admin_kick":
        auth.cerrar_sesion(tg_id)
        await query.answer("Sesión cerrada.")
        await query.message.reply_html(
            f"🚫 Sesión de <code>{tg_id}</code> cerrada por <code>{quien}</code>."
        )
    else:
        auth.remove_user(tg_id)
        auth.cerrar_sesion(tg_id)
        await query.answer("Membresía revocada.")
        await query.message.reply_html(
            f"⛔ Membresía de <code>{tg_id}</code> revocada por <code>{quien}</code>."
        )


def _resumen_usuario(tg_id: int) -> str:
    sesion = auth.sesion_de(tg_id)
    lineas = [f"👤 <code>{tg_id}</code>"]
    lineas.append("✅ Con acceso" if auth.is_whitelisted(tg_id) else "🚫 Sin acceso")
    if sesion:
        lineas.append(f"🟢 Sesión activa desde {_fmt_dt(sesion['created_at'])}")
        lineas.append(f"🌐 IP: {sesion['ip'] or 'desconocida'}")
        lineas.append(f"⏳ Vence: {_fmt_dt(sesion['expires_at'])}")
    else:
        lineas.append("⚪ Sin sesión activa")
    return "\n".join(lineas)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin — lista las sesiones activas en este momento, con botones de
    moderación por cada una."""
    if not auth.is_admin(update.effective_user.id):
        return  # silencio: no confirmamos ni que el comando existe

    sesiones = auth.listar_sesiones_activas()
    if not sesiones:
        await update.message.reply_text("No hay sesiones activas ahora mismo.")
        return

    await update.message.reply_text(f"🟢 {len(sesiones)} sesión(es) activa(s):")
    for s in sesiones:
        texto = (
            f"👤 <code>{s['tg_id']}</code>\n"
            f"🌐 IP: {s['ip'] or 'desconocida'}\n"
            f"🕒 Desde: {_fmt_dt(s['created_at'])}\n"
            f"⏳ Vence: {_fmt_dt(s['expires_at'])}"
        )
        await update.message.reply_html(texto, reply_markup=auth.teclado_moderacion(s["tg_id"]))


async def usuario_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/usuario <telegram_id> — para revisar a alguien puntual aunque no
    haya saltado ninguna alerta automática todavía."""
    if not auth.is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Uso: /usuario <telegram_id>")
        return

    tg_id = int(context.args[0])
    await update.message.reply_html(_resumen_usuario(tg_id), reply_markup=auth.teclado_moderacion(tg_id))


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manda un .py al bot para agregarlo como módulo externo, o un .csv
    para importarlo a la whitelist — mismo resultado que subirlo desde el
    panel Admin, sin depender del selector de archivos del navegador (en
    varios celulares no reconoce estas extensiones y las deja en gris)."""
    if not auth.is_admin(update.effective_user.id):
        return  # silencio, mismo criterio que /admin y /usuario

    documento = update.message.document
    nombre = documento.file_name or ""
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""

    if extension not in ("py", "csv"):
        await update.message.reply_text(
            "Solo acepto .py (módulo externo) o .csv (importar usuarios a la whitelist)."
        )
        return

    archivo = await documento.get_file()
    contenido = bytes(await archivo.download_as_bytearray())

    if extension == "py":
        try:
            module_id_real = sdk.registrar_externo(contenido)
        except Exception as exc:
            logger.exception("No se pudo registrar el módulo desde Telegram (%s)", nombre)
            await update.message.reply_text(f"No se pudo agregar el módulo: {exc}")
        else:
            await update.message.reply_text(f"✅ Módulo '{module_id_real}' agregado como externo.")
        return

    # extension == "csv"
    try:
        texto = contenido.decode("utf-8")
        filas = list(csv.DictReader(io.StringIO(texto)))
        importados, omitidos = auth.import_csv(filas, update.effective_user.id)
    except Exception as exc:
        logger.exception("No se pudo importar el CSV desde Telegram")
        await update.message.reply_text(f"No se pudo importar el CSV: {exc}")
    else:
        await update.message.reply_text(f"✅ Importados: {importados} · Omitidos: {omitidos}")


def _es_canal_anuncios(chat) -> bool:
    configurado = os.getenv("OLIMPO_ANNOUNCE_CHANNEL_ID", "").strip()
    if not configurado:
        return False
    if configurado.startswith("@"):
        return f"@{(chat.username or '').lower()}" == configurado.lower()
    return str(chat.id) == configurado


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Espeja los posts de texto de OLIMPO_ANNOUNCE_CHANNEL_ID hacia la
    pestaña "Anuncios" de la app — el bot necesita ser administrador de
    ese canal para recibir sus posts (requisito de la API de Telegram,
    no algo que se pueda configurar desde acá)."""
    post = update.channel_post or update.edited_channel_post
    if post is None or not _es_canal_anuncios(post.chat):
        return
    texto = (post.text or "").strip()
    if not texto:
        return  # posts sin texto (solo foto/video/etc) no se reflejan por ahora
    canal.guardar_mensaje(post.message_id, texto, post.date.isoformat())


def main() -> None:
    token = os.environ["OLIMPO_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whoami", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("usuario", usuario_lookup))
    app.add_handler(CallbackQueryHandler(on_login_callback, pattern=r"^login_(ok|no):"))
    app.add_handler(CallbackQueryHandler(on_admin_callback, pattern=r"^admin_(kick|ban):"))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POST | filters.UpdateType.EDITED_CHANNEL_POST,
        on_channel_post,
    ))
    logger.info("OLIMPO auth bot iniciado")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
