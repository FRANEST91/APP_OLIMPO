import asyncio
import secrets
from datetime import datetime, timezone

import aiohttp
import streamlit as st

import sdk
from db import get_conn

try:
    from faker import Faker
except ImportError:  # pragma: no cover
    Faker = None

MODULE_ID = "tempmail"
MODULE_NAME = "📧 Correo temporal"
MODULE_VERSION = "1.0.0"
MODULE_AUTHOR = "OLIMPO"
MODULE_DATA_SCOPE = "per_user"  # una cuenta de correo por usuario

API_BASE = "https://api.mail.tm"
TOKEN_TTL_SECONDS = 55 * 60
MAX_MESSAGES = 10
MAX_BODY_CHARS = 3000


def _run(coro):
    return asyncio.run(coro)


def _cuenta_row(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tempmail_cuentas WHERE user_id = ?", (user_id,)
        ).fetchone()


async def _domains(session: aiohttp.ClientSession) -> list:
    async with session.get(f"{API_BASE}/domains") as resp:
        resp.raise_for_status()
        data = await resp.json()
        return [d["domain"] for d in data["hydra:member"]]


async def _crear_cuenta(user_id: int) -> dict:
    async with aiohttp.ClientSession() as session:
        domains = await _domains(session)
        domain = secrets.choice(domains)
        email = f"{secrets.token_hex(6)}@{domain}"
        password = secrets.token_urlsafe(12)

        async with session.post(
            f"{API_BASE}/accounts", json={"address": email, "password": password}
        ) as resp:
            resp.raise_for_status()
            account = await resp.json()

        async with session.post(
            f"{API_BASE}/token", json={"address": email, "password": password}
        ) as resp:
            resp.raise_for_status()
            token_data = await resp.json()

    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tempmail_cuentas
                (user_id, email, password, account_id, token, token_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, email, password, account["id"], token_data["token"], now, now),
        )

    return {"email": email, "account_id": account["id"], "created_at": now}


def crear_cuenta(user_id: int) -> dict:
    # Idempotente: si ya existe una cuenta, la devuelve en vez de crear otra
    # en mail.tm (crear una segunda dejaría la primera huérfana y sin forma
    # de borrarla, porque el schema solo guarda una cuenta por user_id).
    existing = _cuenta_row(user_id)
    if existing is not None:
        return {
            "email": existing["email"],
            "account_id": existing["account_id"],
            "created_at": existing["created_at"],
        }
    return _run(_crear_cuenta(user_id))


async def _renovar_token(email: str, password: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/token", json={"address": email, "password": password}
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["token"]


def get_token(user_id: int) -> str:
    row = _cuenta_row(user_id)
    if row is None:
        raise ValueError(f"No hay cuenta tempmail para user_id={user_id}")

    token_at = datetime.fromisoformat(row["token_at"]) if row["token_at"] else None
    stale = (
        token_at is None
        or (datetime.now(timezone.utc) - token_at).total_seconds() > TOKEN_TTL_SECONDS
    )
    if not row["token"] or stale:
        token = _run(_renovar_token(row["email"], row["password"]))
        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            conn.execute(
                "UPDATE tempmail_cuentas SET token = ?, token_at = ? WHERE user_id = ?",
                (token, now, user_id),
            )
        return token
    return row["token"]


async def _ver_bandeja(token: str) -> list:
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{API_BASE}/messages", params={"page": 1}) as resp:
            resp.raise_for_status()
            data = await resp.json()

    mensajes = []
    for m in data["hydra:member"][:MAX_MESSAGES]:
        mensajes.append(
            {
                "id": m["id"],
                "subject": m.get("subject") or "(sin asunto)",
                "from": m.get("from", {}).get("address", "desconocido"),
                "seen": m.get("seen", False),
                "created_at": m.get("createdAt"),
            }
        )
    return mensajes


def ver_bandeja(user_id: int) -> list:
    token = get_token(user_id)
    return _run(_ver_bandeja(token))


async def _leer_mensaje(token: str, msg_id: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(f"{API_BASE}/messages/{msg_id}") as resp:
            resp.raise_for_status()
            data = await resp.json()

    body = data.get("text") or "".join(data.get("html") or [])
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "…"

    return {
        "id": data["id"],
        "subject": data.get("subject") or "(sin asunto)",
        "from": data.get("from", {}).get("address", "desconocido"),
        "body": body,
        "created_at": data.get("createdAt"),
    }


def leer_mensaje(user_id: int, msg_id: str) -> dict:
    token = get_token(user_id)
    return _run(_leer_mensaje(token, msg_id))


async def _eliminar_mensaje(token: str, msg_id: str) -> bool:
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.delete(f"{API_BASE}/messages/{msg_id}") as resp:
            return resp.status in (200, 204)


def eliminar_mensaje(user_id: int, msg_id: str) -> bool:
    token = get_token(user_id)
    return _run(_eliminar_mensaje(token, msg_id))


MX_LADAS = ["55", "33", "81", "222", "442", "998", "664", "656"]


def _telefono_mx() -> str:
    # fake.phone_number() de Faker (es_MX) mete formatos tipo "x60118"
    # que no existen en México. Se arma un número real de 10 dígitos.
    lada = secrets.choice(MX_LADAS)
    resto = "".join(secrets.choice("0123456789") for _ in range(10 - len(lada)))
    mitad = len(resto) // 2
    return f"+52 {lada} {resto[:mitad]} {resto[mitad:]}"


def generar_identidad(user_id: int) -> dict:
    if Faker is None:
        raise RuntimeError("La librería 'faker' no está instalada")

    fake = Faker("es_MX")
    letras = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    curp = (
        "".join(secrets.choice(letras) for _ in range(4))
        + "".join(secrets.choice("0123456789") for _ in range(6))
        + "".join(secrets.choice(letras + "0123456789") for _ in range(8))
    )

    return {
        "nombre": fake.name(),
        "curp": curp,
        "nss": "".join(secrets.choice("0123456789") for _ in range(11)),
        "telefono": _telefono_mx(),
        "direccion": fake.address().replace("\n", ", "),
    }


async def _eliminar_cuenta(token: str, account_id: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.delete(f"{API_BASE}/accounts/{account_id}") as resp:
            if resp.status not in (200, 204, 404):
                resp.raise_for_status()


def eliminar_cuenta(user_id: int) -> None:
    row = _cuenta_row(user_id)
    if row is None:
        return
    token = get_token(user_id)
    _run(_eliminar_cuenta(token, row["account_id"]))
    with get_conn() as conn:
        conn.execute("DELETE FROM tempmail_cuentas WHERE user_id = ?", (user_id,))


def render(user_id: int) -> None:
    st.subheader(MODULE_NAME)

    row = _cuenta_row(user_id)

    if row is None:
        st.write("Todavía no tienes un correo temporal.")
        if st.button("Crear correo", key=f"{MODULE_ID}_crear"):
            with sdk.api_errors("No se pudo crear el correo"):
                with st.spinner("Creando correo..."):
                    cuenta = crear_cuenta(user_id)
                st.success(f"Listo: {cuenta['email']}")
                st.rerun()
        return

    st.code(row["email"], language=None)

    tab_bandeja, tab_identidad = st.tabs(["Bandeja", "Identidad"])

    with tab_bandeja:
        if st.button("Actualizar bandeja", key=f"{MODULE_ID}_refrescar"):
            st.rerun()
        mensajes = []
        with sdk.api_errors("No se pudo cargar la bandeja"):
            with st.spinner("Cargando mensajes..."):
                mensajes = ver_bandeja(user_id)
        if not mensajes:
            st.write("Bandeja vacía.")
        for m in mensajes:
            icono = "📨" if m["seen"] else "📩"
            with st.expander(f"{icono} {m['subject']} — {m['from']}"):
                if st.button("Leer", key=f"{MODULE_ID}_leer_{m['id']}"):
                    with sdk.api_errors("No se pudo leer el mensaje"):
                        detalle = leer_mensaje(user_id, m["id"])
                        st.write(detalle["body"])
                if st.button("Eliminar", key=f"{MODULE_ID}_del_{m['id']}"):
                    with sdk.api_errors("No se pudo eliminar el mensaje"):
                        eliminar_mensaje(user_id, m["id"])
                        st.rerun()

    with tab_identidad:
        estado_key = f"{MODULE_ID}_identidad"
        if estado_key not in st.session_state:
            with sdk.api_errors("No se pudo generar la identidad"):
                st.session_state[estado_key] = generar_identidad(user_id)
        identidad = st.session_state.get(estado_key)
        if identidad:
            for campo, valor in identidad.items():
                st.caption(campo.capitalize())
                st.code(valor, language=None)
        if st.button("Regenerar identidad", key=f"{MODULE_ID}_regenerar"):
            with sdk.api_errors("No se pudo generar la identidad"):
                st.session_state[estado_key] = generar_identidad(user_id)
                st.rerun()

    st.divider()
    if st.button("Borrar correo", key=f"{MODULE_ID}_borrar"):
        with sdk.api_errors("No se pudo borrar el correo"):
            eliminar_cuenta(user_id)
            st.rerun()
