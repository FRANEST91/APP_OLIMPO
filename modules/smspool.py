import asyncio
import os
import time
from datetime import datetime, timezone

import aiohttp

from db import get_conn

API_BASE = "https://api.smspool.net"
CACHE_TTL_SECONDS = 6 * 60 * 60
POLL_INTERVAL_SECONDS = 15

# Cache en memoria: {"paises" | "servicios": (timestamp, lista)}
_cache: dict = {}


def _run(coro):
    return asyncio.run(coro)


def _api_key() -> str:
    key = os.getenv("SMSPOOL_API_KEY")
    if not key:
        raise RuntimeError("SMSPOOL_API_KEY no está configurada")
    return key


def _cached(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < CACHE_TTL_SECONDS:
        return entry[1]
    return None


async def _get(path: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}{path}") as resp:
            resp.raise_for_status()
            return await resp.json()


async def _post(path: str, data: dict) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_BASE}{path}", data=data) as resp:
            resp.raise_for_status()
            return await resp.json()


def listar_paises() -> list:
    cached = _cached("paises")
    if cached is not None:
        return cached
    data = _run(_get("/country/retrieve_all"))
    paises = [{"id": pid, "nombre": nombre} for pid, nombre in data.items()]
    _cache["paises"] = (time.time(), paises)
    return paises


def listar_servicios() -> list:
    cached = _cached("servicios")
    if cached is not None:
        return cached
    data = _run(_get("/service/retrieve_all"))
    servicios = [{"id": sid, "nombre": nombre} for sid, nombre in data.items()]
    _cache["servicios"] = (time.time(), servicios)
    return servicios


def comprar_numero(user_id: int, country_id: str, service_id: str) -> dict:
    data = _run(
        _post(
            "/purchase/sms",
            {"country": country_id, "service": service_id, "apikey": _api_key()},
        )
    )
    order_id = str(data["order_id"])
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO olimpo_sms_orders
                (user_id, order_id, phone_number, service_name, country_name,
                 status, requested_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (user_id, order_id, data["number"], service_id, country_id, now),
        )

    return {
        "order_id": order_id,
        "number": data["number"],
        "expires_in": data.get("expires_in"),
    }


def check_sms(order_id: str) -> dict:
    # El código llega en el campo "sms", NUNCA en "code" (ese campo no existe).
    data = _run(_post("/sms/check", {"orderid": order_id, "apikey": _api_key()}))
    status = data.get("status")
    sms = data.get("sms")

    if str(status) == "3" and sms:
        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE olimpo_sms_orders
                SET sms_code = ?, status = 'completed', completed_at = ?
                WHERE order_id = ?
                """,
                (sms, now, order_id),
            )

    return {"status": status, "sms": sms}


def cancelar(order_id: str) -> bool:
    data = _run(_post("/sms/cancel", {"orderid": order_id, "apikey": _api_key()}))
    ok = bool(data.get("success", True))
    if ok:
        with get_conn() as conn:
            conn.execute(
                "UPDATE olimpo_sms_orders SET status = 'cancelled' WHERE order_id = ?",
                (order_id,),
            )
    return ok


def poll_hasta_codigo(order_id: str, timeout: int = 90):
    elapsed = 0
    while elapsed < timeout:
        result = check_sms(order_id)
        if str(result["status"]) == "3" and result["sms"]:
            return result["sms"]
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE olimpo_sms_orders SET status = 'failed'
            WHERE order_id = ? AND status = 'pending'
            """,
            (order_id,),
        )
    return None
