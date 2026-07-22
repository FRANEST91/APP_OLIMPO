"""Plantilla para módulos nuevos de OLIMPO.

Copia este archivo a modules/<nombre>.py y adapta lo marcado con TODO.
Sigue el mismo patrón que modules/tempmail.py y modules/smspool.py:

- Las llamadas HTTP van en funciones async (aiohttp, ya está en
  requirements.txt) porque las APIs externas suelen ser async-first;
  cada función pública que use la UI es sync y usa _run() para
  ejecutar el coroutine, porque Streamlit corre sync.
- Todo lo que toque SQLite pasa por db.get_conn() (ver db.py). Si tu
  módulo necesita guardar datos, agrega una tabla nueva en db.SCHEMA.
- No hace falta try/except propio acá: app.py ya envuelve las llamadas
  a los módulos con _api_errors(), que muestra el error en la UI. Si
  una excepción necesita un mensaje más claro, alcanza con un
  raise ValueError("mensaje") descriptivo.
"""

import asyncio

import aiohttp

from db import get_conn

API_BASE = "https://api.ejemplo.com"  # TODO: URL base de la API externa


def _run(coro):
    return asyncio.run(coro)


async def _mi_funcion(user_id: int, algo: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}/algo/{algo}") as resp:
            resp.raise_for_status()
            return await resp.json()


def mi_funcion(user_id: int, algo: str) -> dict:
    # TODO: nombra esta función según lo que hace, en español y en
    # minúsculas (igual que crear_cuenta, comprar_numero, etc.)
    return _run(_mi_funcion(user_id, algo))


def guardar_algo(user_id: int, valor: str) -> None:
    # TODO: ejemplo de escritura en SQLite. Borra esto si tu módulo no
    # necesita persistencia, o agrega la tabla real en db.py antes de
    # usar este patrón.
    with get_conn() as conn:
        conn.execute(
            "UPDATE mi_tabla SET valor = ? WHERE user_id = ?",
            (valor, user_id),
        )
