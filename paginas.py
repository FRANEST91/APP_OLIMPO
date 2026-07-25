from datetime import datetime, timezone

from db import get_conn


def agregar_pagina(titulo: str, emoji: str, contenido: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO paginas (titulo, emoji, contenido, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (titulo, emoji or "📄", contenido, now),
        )
        return cur.lastrowid


def listar_paginas(solo_activas: bool = True) -> list:
    query = "SELECT * FROM paginas"
    if solo_activas:
        query += " WHERE active = 1"
    query += " ORDER BY id ASC"
    with get_conn() as conn:
        return conn.execute(query).fetchall()


def actualizar_pagina(pagina_id: int, titulo: str, emoji: str, contenido: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE paginas SET titulo = ?, emoji = ?, contenido = ? WHERE id = ?",
            (titulo, emoji or "📄", contenido, pagina_id),
        )


def toggle_activo(pagina_id: int, activo: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE paginas SET active = ? WHERE id = ?", (1 if activo else 0, pagina_id))


def eliminar_pagina(pagina_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM paginas WHERE id = ?", (pagina_id,))
