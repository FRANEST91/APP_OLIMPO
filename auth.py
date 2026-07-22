import os
import secrets
import time

from telegram import Bot
from telegram.constants import ParseMode

OTP_TTL_SECONDS = 300
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
        text=f"🔐 Tu código OLIMPO: <b>{code}</b>\nExpira en 5 minutos.",
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


def is_whitelisted(tg_id: int) -> bool:
    ids = [x.strip() for x in os.getenv("OLIMPO_WHITELIST", "").split(",") if x.strip()]
    return str(tg_id) in ids
