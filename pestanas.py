"""Orden, nombre y emoji de las pestañas de la app — aplica por igual a
Inicio, cada módulo activo, Anuncios, cada página de contenido y Admin.

Cada pestaña tiene una "tab_key" estable ("inicio", "anuncios", "admin",
"modulo:<module_id>", "pagina:<id>") con la que se guarda el override acá.
Si no hay fila para una tab_key, se usan los valores de fábrica que arma
app.py — nombre/emoji en blanco no es un override, es "usar el de fábrica".
"""

from db import get_conn


def set_config(tab_key: str, nombre: str | None, emoji: str | None, orden: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO pestanas_config (tab_key, nombre, emoji, orden)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tab_key) DO UPDATE SET
                nombre = excluded.nombre, emoji = excluded.emoji, orden = excluded.orden
            """,
            (tab_key, nombre or None, emoji or None, orden),
        )


def get_all_config() -> dict:
    """tab_key -> {"nombre": str|None, "emoji": str|None, "orden": int}"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM pestanas_config").fetchall()
    return {r["tab_key"]: dict(r) for r in rows}
