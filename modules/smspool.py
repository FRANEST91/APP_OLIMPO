import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

import aiohttp

from db import get_conn

API_BASE = "https://api.smspool.net"
CACHE_TTL_SECONDS = 6 * 60 * 60
CANCEL_WINDOW_SECONDS = 10

# Cache en memoria: {"servicios" | "paises_<service_id>": (timestamp, lista)}
_cache: dict = {}


def _run(coro):
    return asyncio.run(coro)


def _api_key() -> str:
    key = os.getenv("SMSPOOL_API_KEY")
    if not key:
        raise RuntimeError("SMSPOOL_API_KEY no está configurada")
    return key


def get_config(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM smspool_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _cached(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < CACHE_TTL_SECONDS:
        return entry[1]
    return None


async def _get(path: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}{path}") as resp:
            body = await resp.text()
            if resp.status >= 400:
                # El body suele traer el motivo real (saldo, parámetro
                # inválido, etc.) que resp.raise_for_status() no muestra.
                raise RuntimeError(f"smspool {path} devolvió {resp.status}: {body}")
            return json.loads(body)


async def _post(path: str, extra: dict | None = None) -> dict:
    # SMSPool exige la API key en el campo "key" (no "apikey") en el body
    # de todo POST autenticado.
    payload = {"key": _api_key(), **(extra or {})}
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_BASE}{path}", data=payload) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"smspool {path} devolvió {resp.status}: {body}")
            return json.loads(body)


def _normalizar_items(data) -> list:
    # La API devuelve una lista de objetos (no un dict), y los nombres de
    # campo varían entre endpoints/mayúsculas ("ID"/"id", "name"/"nombre").
    items = []
    if isinstance(data, dict):
        data = data.get("data") or data.get("result") or []
    for entry in data or []:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("ID") or entry.get("id")
        item_name = entry.get("name") or entry.get("nombre") or entry.get("country")
        if item_id is None or item_name is None:
            continue
        items.append({"id": str(item_id), "nombre": item_name})
    return items


def listar_servicios() -> list:
    cached = _cached("servicios")
    if cached is not None:
        return cached
    data = _run(_get("/service/retrieve_all"))
    servicios = _normalizar_items(data)
    _cache["servicios"] = (time.time(), servicios)
    return servicios


def _calc_credits(usd_price) -> int:
    """USD → MXN (tasa configurable en smspool_config) → tramo de créditos."""
    try:
        usd = float(usd_price)
        rate = float(get_config("usd_to_mxn", "18.5") or "18.5")
        mxn = usd * rate
        if mxn <= 5:
            return 10
        elif mxn <= 10:
            return 20
        elif mxn <= 15:
            return 30
        elif mxn <= 20:
            return 40
    except Exception:
        pass
    return 0


def listar_paises_servicio(service_id: str) -> list:
    """Países sugeridos para un servicio, con su precio ya convertido a
    créditos. Los países fuera de tramo (> $20 MXN) se excluyen."""
    cache_key = f"paises_{service_id}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    data = _run(_post("/request/suggested_countries", {"service": service_id}))
    paises = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        country_id = entry.get("country_id")
        name = entry.get("name")
        if country_id is None or name is None:
            continue
        credits = _calc_credits(entry.get("price", "0"))
        if credits <= 0:
            continue
        paises.append({
            "id": str(country_id),
            "nombre": name,
            "usd": str(entry.get("price", "0")),
            "creditos": credits,
            "short_name": (entry.get("short_name") or "").upper(),
        })

    # México siempre primero si está disponible
    mx = next((p for p in paises if p["short_name"] == "MX"), None)
    if mx:
        paises = [mx] + [p for p in paises if p is not mx]

    _cache[cache_key] = (time.time(), paises)
    return paises


def comprar_numero(country_id: str, service_id: str) -> dict:
    data = _run(
        _post("/purchase/sms", {
            "country": country_id,
            "service": service_id,
            "pricing_option": 0,
        })
    )
    if not data.get("success"):
        raise RuntimeError(data.get("message", "SMSPool rechazó la compra"))

    order_id = str(data.get("order_id") or "")
    if not order_id:
        raise RuntimeError("Respuesta de SMSPool sin order_id")

    return {
        "order_id": order_id,
        "number": data.get("number"),
        "expires_in": int(data.get("expires_in") or 1200),
    }


def registrar_pedido(
    user_id: int, order_id: str, phone_number: str, service_name: str,
    country_name: str, credits_charged: int, expires_in: int,
) -> None:
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=expires_in)).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO olimpo_sms_orders
                (user_id, order_id, phone_number, service_name, country_name,
                 status, requested_at, credits_charged, expires_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (user_id, order_id, phone_number, service_name, country_name,
             now.isoformat(), credits_charged, expires_at),
        )


def check_sms(order_id: str) -> dict:
    # El código llega en el campo "sms", NUNCA en "code" (ese campo no existe).
    data = _run(_post("/sms/check", {"orderid": order_id}))
    status = data.get("status")
    sms = str(data.get("sms") or "")
    code_valido = bool(sms and sms not in ("0", "", "None"))

    if code_valido:
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

    return {"status": status, "sms": sms if code_valido else None}


def cancelar(order_id: str) -> None:
    try:
        _run(_post("/sms/cancel", {"orderid": order_id}))
    except Exception:
        # Si SMSPool ya expiró/canceló el número por su cuenta, igual
        # queremos reflejar la cancelación y devolver los créditos.
        pass
    with get_conn() as conn:
        conn.execute(
            "UPDATE olimpo_sms_orders SET status = 'cancelled' WHERE order_id = ?",
            (order_id,),
        )


def marcar_fallido(order_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE olimpo_sms_orders SET status = 'failed', completed_at = ?
            WHERE order_id = ? AND status = 'pending'
            """,
            (now, order_id),
        )


def esta_expirado(expires_at: str) -> bool:
    try:
        limite = datetime.fromisoformat(expires_at)
        if limite.tzinfo is None:
            limite = limite.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= limite
    except Exception:
        return False
