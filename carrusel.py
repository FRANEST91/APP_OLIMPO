from datetime import datetime, timezone

from db import get_conn

MIME_PERMITIDOS = {"image/png", "image/jpeg", "image/gif"}


def agregar_imagen(nombre: str, contenido: bytes, mime_type: str, duracion_ms: int) -> None:
    if mime_type not in MIME_PERMITIDOS:
        raise ValueError(f"Formato no soportado: {mime_type}")
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        orden_max = conn.execute("SELECT COALESCE(MAX(orden), -1) FROM carrusel").fetchone()[0]
        conn.execute(
            """
            INSERT INTO carrusel (nombre, contenido, mime_type, orden, duracion_ms, active, uploaded_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (nombre, contenido, mime_type, orden_max + 1, duracion_ms, now),
        )


def listar_imagenes(solo_activas: bool = True) -> list:
    query = "SELECT * FROM carrusel"
    if solo_activas:
        query += " WHERE active = 1"
    query += " ORDER BY orden ASC"
    with get_conn() as conn:
        return conn.execute(query).fetchall()


def actualizar_duracion(imagen_id: int, duracion_ms: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE carrusel SET duracion_ms = ? WHERE id = ?", (duracion_ms, imagen_id))


def actualizar_orden(imagen_id: int, orden: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE carrusel SET orden = ? WHERE id = ?", (orden, imagen_id))


def actualizar_texto(imagen_id: int, texto_arriba: str | None, texto_abajo: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE carrusel SET texto_arriba = ?, texto_abajo = ? WHERE id = ?",
            (texto_arriba, texto_abajo, imagen_id),
        )


def toggle_activo(imagen_id: int, activo: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE carrusel SET active = ? WHERE id = ?", (1 if activo else 0, imagen_id))


def eliminar_imagen(imagen_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM carrusel WHERE id = ?", (imagen_id,))
